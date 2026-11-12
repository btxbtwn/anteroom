"""The existing application's account-scoped HTTP contract.

Every query uses the verified seller session. No desktop services are imported
or started, and no request accepts an owner, file path or operational identity.
"""
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import defer

from vendoo_studio.database import json_value

from vendoo_studio.models.conversation import Conversation, Message, Photo
from vendoo_studio.models.job import ACTIVE_JOB_STATUSES, Job
from vendoo_studio.models.listing import Listing, ListingRevision
from vendoo_studio.models.marketplace_listing import LIVE_STATES
from vendoo_studio.services.validation import etsy_eligible, validate_listing
from vendoo_studio.repositories import hosted_drafts
from vendoo_studio.routes import api_contract as contract
from vendoo_studio.routes.api_contract import CONFLICT, NOT_FOUND, REFUSED
from vendoo_studio.routes.hosted_direct_listing import ACTION_MARKETPLACES
from vendoo_studio.routes.suggestions import SuggestionsResponse
from vendoo_studio.services import user_settings
from vendoo_studio.services.hosted_database import get_seller_db
from vendoo_studio.services.hosted_listing_contract import editable_listing, stored_listing_fields
from vendoo_studio.version import app_version

router = APIRouter(prefix='/api')


def conversation(db, identity, *, lock=False):
    row = db.get(Conversation, identity, with_for_update=True if lock else None)
    if row is None:
        raise HTTPException(404, 'Listing not found')
    return row


def assert_idle(db, identity):
    from vendoo_studio.services.background_tasks import require_idle
    require_idle(db, identity)
    if db.scalar(select(Job.id).where(Job.conversation_id == identity, Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)):
        raise HTTPException(409, 'Wait for the current listing action to finish')


def check_box(db, box_id):
    if box_id:
        from vendoo_studio.models.sourcing import SourceBox
        if db.get(SourceBox, box_id) is None:
            raise HTTPException(404, 'Box not found')


def iso(value):
    return value.isoformat() if value else None


def conversation_response(row, listing=None, direct=None, *, notes=None, include_notes=True, missing_sale_data=None, sold=None):
    """One inventory row. ``notes`` are the parsed notes when the caller deferred loading them.

    ``sold`` is the item's status from its sales: the Sold tab sorts by its
    sale date, the one Analytics shows.
    """
    from vendoo_studio.services.imported_listing import imported_fields
    from vendoo_studio.services.vendoo_import import parse_notes

    if notes is None:
        notes = parse_notes(row.notes)
    result = {'id': row.id, 'title': row.title, 'status': row.status,
            'box_id': row.box_id, 'settled_at': iso(row.settled_at), 'unsettled_at': iso(row.unsettled_at),
            'created_at': iso(row.created_at) or '', 'updated_at': iso(row.updated_at) or '',
            'sku': (listing or {}).get('sku'), 'price': (listing or {}).get('price'),
            'quantity': (listing or {}).get('quantity'),
            'direct_marketplaces': [market for market, record in (direct or {}).items() if record['state'] in LIVE_STATES],
            'direct_records': direct or {},
            'units_sold': sold.units_sold if sold else 0,
            'sold_at': sold.sold_at.isoformat() if sold and sold.sold_at else None,
            'missing_sale_data': missing_sale_data or [], **imported_fields(notes, direct or {})}
    if include_notes:
        result['notes'] = row.notes
    return result


def listing_response(db, identity):
    revision = hosted_drafts.current_fields_revision(db, identity)
    raw = revision.listing_json if revision and isinstance(revision.listing_json, dict) else {}
    payload = stored_listing_fields(raw)
    if 'title' not in payload:
        payload['title'] = conversation(db, identity).title or ''
    count = db.scalar(select(func.count()).select_from(Photo).where(Photo.conversation_id == identity))
    import copy
    validation = validate_listing(copy.deepcopy(payload), count, require_photos=True)
    return {'conversation_id': identity, 'current_revision_id': revision.id if revision else None,
            'listing': payload, 'revision_count': db.scalar(select(func.count()).select_from(ListingRevision)
                .where(ListingRevision.conversation_id == identity)), 'can_send': validation.can_send,
            'errors': validation.errors, 'warnings': validation.warnings, 'etsy_eligible': etsy_eligible(payload)}


@router.get('/runtime', response_model=contract.WorkspaceRuntime)
def runtime(request: Request, db=Depends(get_seller_db, scope="function")):
    from vendoo_studio.services.category_tree import categories_ready
    from vendoo_studio.routes.hosted_tasks import drafts_run, schedules_run
    generation, scheduled = drafts_run(request.app), schedules_run(request.app)
    return {'mode': 'hosted', 'features': ['inventory', 'listing_fields', 'photos', 'preferences', 'photo_edits', 'bulk_actions', 'bulk_edit', 'bulk_copy', 'bulk_delete', 'bulk_markdowns', 'suggestions', 'assistant', 'account_deletion']
            + (['marketplace_sessions'] if scheduled else [])
            + (['generation', 'chat_attachments', 'bulk_regenerate'] if generation else [])
            + (['catalog_categories'] if categories_ready(db) else [])
            + (['ebay_offers'] if scheduled else []),
            'views': ['listings', 'analytics', 'settings', 'automations', 'assistant', 'marketplaces', 'sourcing'], 'marketplace_forms': ['poshmark', 'depop', 'mercari', 'ebay', 'etsy'],
            # eBay and Etsy list through their APIs, the rest through the seller's Chrome; a restart's held slot needs the job watchdog.
            'action_marketplaces': list(ACTION_MARKETPLACES) if scheduled else []}


@router.get('/status')
def status(db=Depends(get_seller_db, scope="function")):
    from vendoo_studio.services.listing_provider import provider_is_configured
    active = db.scalars(select(Job.id).where(Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)).first()
    return {'version': app_version(), 'provider_configured': provider_is_configured(), 'extension_connected': False,
            'active_job_id': active, 'conversations': db.scalar(select(func.count()).select_from(Conversation)),
            'database_ok': True, 'packaged': False, 'chrome_available': False, 'setup_guide_dismissed': True}


@router.get('/conversations', response_model=list[contract.ConversationSummary])
async def list_conversations(request: Request):
    return await request.app.state.seller_reads.read('inventory', inventory_response)


@router.get('/suggestions', response_model=SuggestionsResponse)
async def suggestions(request: Request):
    from vendoo_studio.services.suggestions import list_suggestions

    return await request.app.state.seller_reads.read('suggestions',
                                                    lambda db: {'suggestions': list_suggestions(db)}, refresh_seconds=60)


def inventory_response(db):
    from vendoo_studio.repositories.queries import ConversationRepo

    # The shared sidebar filters stored inventory status. Reconcile it from
    # listings and paid sales just as the desktop list endpoint does.
    from vendoo_studio.services.inventory_analytics import read_analytics_inputs, rows_from_inputs

    analytics = read_analytics_inputs(db)
    ConversationRepo(db).reconcile_item_statuses(
        [row for row in db.scalars(select(Conversation).options(defer(Conversation.notes))).all()
         if row.id in analytics.statuses],
        {identity: notes for identity, _title, _status, notes, _box in analytics.conversations},
        analytics.statuses)
    # Keep the existing sidebar's complete-list contract; it filters and
    # virtualizes locally. No historical pointers or fallback file URLs escape.
    rows = db.scalars(select(Conversation).options(defer(Conversation.notes)).order_by(Conversation.updated_at.desc(), Conversation.id.desc())).all()
    reconciled = {row.id: row.status for row in rows}
    # Parsed once for analytics; the rows themselves leave notes unloaded.
    notes = {identity: parsed for identity, _title, _status, parsed, _box in analytics.conversations}
    # Another device may create or delete a draft between these committed reads.
    analytics.conversations = [(identity, title, reconciled.get(identity, status), notes, box)
                              for identity, title, status, notes, box in analytics.conversations]
    ordered = select(Photo.conversation_id, Photo.id, Photo.checksum, func.row_number().over(
        partition_by=Photo.conversation_id, order_by=(Photo.display_order, Photo.id)).label('position')).where(
            Photo.marketplace.is_(None)).subquery()
    covers = {identity: (photo_id, checksum) for identity, photo_id, checksum in db.execute(
        select(ordered.c.conversation_id, ordered.c.id, ordered.c.checksum).where(ordered.c.position == 1))}
    current = {}
    for identity, sku, price, quantity in db.execute(select(Listing.conversation_id,
            json_value(ListingRevision.inventory_fields, 'sku'), json_value(ListingRevision.inventory_fields, 'price'),
            json_value(ListingRevision.inventory_fields, 'quantity')).join(
            ListingRevision, (ListingRevision.id == Listing.current_revision_id)
            & (ListingRevision.conversation_id == Listing.conversation_id)
            & (ListingRevision.user_id == Listing.user_id))):
        current[identity] = {'sku': sku, 'price': float(price) if price is not None else None,
                             'quantity': int(quantity) if quantity is not None else None}
    from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo

    direct = MarketplaceListingRepo(db).summaries()
    from vendoo_studio.services.sales_records import read_sale_lines, records_from_lines, missing_from_records

    sales = read_sale_lines(db)
    from vendoo_studio.services.unsent_edits import unsent_edits

    unsent = unsent_edits(db, rows, ConversationRepo(db).current_revision_ids())
    # Ledger validation, cost allocation and hashing are CPU work over loaded
    # values. Keep scarce connections available while that work runs.
    db.close()
    missing = missing_from_records(records_from_lines(sales, rows_from_inputs(analytics)))
    summaries = []
    for row in rows:
        summary = conversation_response(row, current.get(row.id), direct.get(row.id), notes=notes.get(row.id, {}), include_notes=False,
                                        missing_sale_data=missing.get(row.id, []), sold=analytics.statuses.get(row.id))
        summary['unsent_edits'] = row.id in unsent
        if row.id in covers:
            from vendoo_studio.services.photos import cover_thumb_url
            summary['cover_photo_url'] = cover_thumb_url(*covers[row.id])
        summaries.append(summary)
    return summaries


@router.post('/conversations', response_model=contract.Conversation, responses=NOT_FOUND)
def create_conversation(body: contract.ConversationInput, db=Depends(get_seller_db, scope="function")):
    check_box(db, body.box_id)
    row = Conversation(title=body.title or 'New Listing', notes=body.notes or '', box_id=body.box_id, status='draft')
    db.add(row)
    db.commit()
    db.refresh(row)
    return conversation_response(row)


@router.get('/conversations/{identity}', response_model=contract.Conversation, responses=NOT_FOUND)
def get_conversation(identity: str, db=Depends(get_seller_db, scope="function")):
    from vendoo_studio.services.item_status import status_for
    from vendoo_studio.services.sales_records import missing_by_conversation
    from vendoo_studio.services.vendoo_import import parse_notes

    row = conversation(db, identity)
    return conversation_response(row, missing_sale_data=missing_by_conversation(db, identity).get(identity, []),
                                 sold=status_for(db, identity, parse_notes(row.notes)))


@router.patch('/conversations/{identity}', response_model=contract.Conversation, responses={**REFUSED, **NOT_FOUND, **CONFLICT})
def edit_conversation(identity: str, body: contract.ConversationInput, db=Depends(get_seller_db, scope="function")):
    row = conversation(db, identity, lock=True)
    assert_idle(db, identity)
    if "box_id" in body.model_fields_set:
        check_box(db, body.box_id)
    changes = body.model_dump(exclude_unset=True)
    note_changes = changes.pop('note_changes', None)
    for key, value in changes.items():
        setattr(row, key, value)
    if note_changes:
        from vendoo_studio.services.vendoo_import import merge_note_changes
        row.notes = merge_note_changes(row.notes, note_changes)
    db.commit()
    db.refresh(row)
    return conversation_response(row)


@router.get('/conversations/{identity}/screen')
def listing_screen(identity: str, db=Depends(get_seller_db, scope="function")):
    """Core editor reads share one seller transaction; marketplace lookups stay separate."""
    # Keep the owned row alive in the identity map for the constituent reads.
    row = conversation(db, identity)
    loaded_messages = messages(identity, db)
    # The contract parts read exactly as their own routes answer them, so a
    # client can seed those reads from this one.
    payload = {'conversation': contract.Conversation.model_validate(conversation_response(row)).model_dump(mode='json'),
            'listing': contract.ListingDocument.model_validate(get_listing(identity, db)).model_dump(mode='json'),
            'photos': [contract.ListingPhoto.model_validate(photo).model_dump(mode='json') for photo in photos(identity, db=db)],
            'messages': loaded_messages,
            'jobs': jobs(identity, db),
            'activity': activity_response(db, identity, len(loaded_messages),
                loaded_messages[-1]['id'] if loaded_messages else None)}
    # The DTO is fully materialized. Encoding and sending it need no transaction.
    db.close()
    return payload


@router.get('/conversations/{identity}/listing', response_model=contract.ListingDocument, responses=NOT_FOUND)
def get_listing(identity: str, db=Depends(get_seller_db, scope="function")):
    conversation(db, identity)
    return listing_response(db, identity)


@router.put('/conversations/{identity}/listing', response_model=contract.ListingSaveResult, responses={**NOT_FOUND, **CONFLICT})
def save_listing(identity: str, body: contract.ListingSave, db=Depends(get_seller_db, scope="function")):
    import copy
    payload = copy.deepcopy(editable_listing(body.listing))
    conversation(db, identity, lock=True)
    assert_idle(db, identity)
    from vendoo_studio.services.hosted_listing_contract import validate_media
    validate_media(db, identity, payload)
    before = hosted_drafts.current_fields_revision(db, identity)
    current = db.get(Listing, identity)
    # Inspect the protected pointer as well as the joined revision so a corrupt
    # soft reference cannot be silently replaced as if no listing existed.
    expected = current.current_revision_id if current else ''
    if body.expected_revision_id != (expected or ''):
        raise HTTPException(409, 'This listing changed on another device. Reload before saving again.')
    if current and expected and before is None:
        raise HTTPException(409, 'This listing needs its revision link repaired before editing.')
    from vendoo_studio.services.agent_categories import match_general_category
    from vendoo_studio.services.seller_overrides import prepare_user_listing

    previous = before.listing_json if before else {}
    prepare_user_listing(previous, payload)
    match_general_category(db, previous, payload)
    revision = ListingRevision(conversation_id=identity, listing_json=payload, source='user_form',
                               parent_revision_id=before.id if before else None)
    db.add(revision)
    db.flush()
    if current is None:
        current = Listing(conversation_id=identity)
        db.add(current)
    current.current_revision_id = revision.id
    current.validation_status = None
    current.validation_errors = None
    db.execute(update(Conversation).where(Conversation.id == identity).values(
        title=payload.get('title') or conversation(db, identity).title,
        updated_at=datetime.now(UTC).replace(tzinfo=None)))
    db.commit()
    result = listing_response(db, identity)
    return {'ok': True, 'revision_id': result['current_revision_id'], 'listing': result['listing'],
            'validation': {'valid': not result['errors'], 'can_send': result['can_send'],
                           'errors': result['errors'], 'warnings': result['warnings'], 'info': []},
            'etsy_eligible': result['etsy_eligible']}


@router.get('/conversations/{identity}/messages')
def messages(identity: str, db=Depends(get_seller_db, scope="function")):
    conversation(db, identity)
    rows = db.scalars(select(Message).where(Message.conversation_id == identity).order_by(Message.created_at, Message.id)).all()
    return [{'id': row.id, 'conversation_id': identity, 'role': row.role, 'text': row.text,
             'provider': row.provider, 'model': row.model, 'created_at': row.created_at,
             'attachments': row.attachments or []} for row in rows]


@router.get('/conversations/{identity}/activity')
def activity(identity: str, db=Depends(get_seller_db, scope="function")):
    conversation(db, identity)
    count = db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == identity))
    last = db.scalar(select(Message.id).where(Message.conversation_id == identity).order_by(Message.created_at.desc()).limit(1))
    return activity_response(db, identity, count, last)


def activity_response(db, identity, count, last):
    jobs = db.scalars(select(Job).where(Job.conversation_id == identity, Job.status.in_(ACTIVE_JOB_STATUSES))).all()
    from vendoo_studio.models.background_task import BackgroundTask
    tasks = db.scalars(select(BackgroundTask).where(
        BackgroundTask.conversation_id == identity, BackgroundTask.status.in_(('queued', 'running')))).all()
    labels = [f'{row.kind}: {row.status}' for row in tasks]
    from vendoo_studio.services.native_marketplaces import is_direct_action, step_label

    items = [step_label(str(row.current_step or '')) or row.current_step or row.status for row in jobs]
    writing = bool(tasks) or any(not is_direct_action(str(row.current_step or '')) for row in jobs)
    return {'busy': bool(jobs or tasks), 'writing': writing, 'items': items + labels,
            'message_count': count, 'last_message_id': last, 'agents': []}


class UiInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    theme: Literal['dark', 'light', 'system'] | None = None
    listing_mode: Literal["direct", "vendoo"] | None = None
    listing_names: Literal['title', 'sku'] | None = None
    settled_shelf_expanded: bool | None = None
    stale_listing_days: int | None = Field(None, ge=15, le=120)
    recent_vendoo_labels: list[str] | None = Field(None, max_length=12)
    remember_labels: str | list[str] | None = None
    restore_labels: list[str] | None = None
    forget_label: str | None = None
    model_favorites: list[dict] | None = None
    label_colors: dict[str, Literal["gray", "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink"]] | None = Field(None, max_length=200)


@router.get('/settings/ui')
def ui(db=Depends(get_seller_db, scope="function")):
    return {'ok': True, **user_settings.get_ui_prefs()}


@router.put('/settings/ui')
def save_ui(body: UiInput, db=Depends(get_seller_db, scope="function")):
    from vendoo_studio.routes.settings import UiPrefsConfig, put_ui_prefs
    put_ui_prefs(UiPrefsConfig(**body.model_dump(exclude_unset=True, exclude_none=True)), db)
    return ui(db)



def job_response(row):
    return {key: getattr(row, key) for key in ('id', 'conversation_id', 'approved_revision_id', 'status',
        'current_step', 'attempt_count', 'last_error', 'created_at', 'updated_at')}


@router.get('/jobs')
def jobs(conversation_id: str | None = None, db=Depends(get_seller_db, scope="function")):
    # The list shows status only; each job's listing copy is tens of kilobytes.
    statement = select(Job).options(defer(Job.listing_snapshot, raiseload=True))
    if conversation_id:
        conversation(db, conversation_id)
        statement = statement.where(Job.conversation_id == conversation_id)
    return [job_response(row) for row in db.scalars(statement.order_by(Job.created_at.desc()).limit(100)).all()]


@router.get('/jobs/queue')
def queue(db=Depends(get_seller_db, scope="function")):
    rows = db.scalars(select(Job).where(Job.status.in_(ACTIVE_JOB_STATUSES)).order_by(Job.created_at)).all()
    return {'work': [], 'jobs': [job_response(row) for row in rows]}


@router.get('/conversations/{identity}/regenerate-details')
def regenerate_details(identity: str, db=Depends(get_seller_db, scope="function")):
    """The Item Details Regenerate will start from, for the bulk review. Nothing is saved."""
    from vendoo_studio.services.hosted_listing_delete import carried_notes
    row = conversation(db, identity)
    return {'notes': carried_notes(db, identity, row.notes)}


class ResetInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    keep_inputs: bool = False


@router.post('/conversations/{identity}/reset')
def reset_conversation(identity: str, request: Request, body: ResetInput | None = None, db=Depends(get_seller_db, scope="function")):
    """Clear, or with keep_inputs Regenerate's wipe. Never contacts a marketplace."""
    from vendoo_studio.services import hosted_listing_delete
    from vendoo_studio.services.hidden_fields import clear_listing_hidden_fields
    conversation(db, identity, lock=True)
    assert_idle(db, identity)
    try:
        keys = hosted_listing_delete.reset_unsent_draft(db, identity, keep_inputs=bool(body and body.keep_inputs))
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    db.commit()
    hosted_listing_delete.release(keys, request.headers['authorization'].partition(' ')[2])
    clear_listing_hidden_fields(identity)
    return conversation_response(conversation(db, identity))



def photo_response(row):
    return {'id': row.id, 'conversation_id': row.conversation_id, 'original_filename': row.original_filename,
            'mime_type': row.mime_type, 'size_bytes': row.size_bytes, 'display_order': row.display_order,
            'width': row.width, 'height': row.height, 'created_at': iso(row.created_at) or '', 'edits': row.edits,
            'marketplace': row.marketplace,
            'url': f'/api/conversations/{row.conversation_id}/photos/{row.id}/bytes'
                + (f'?v={row.checksum[:12]}' if row.checksum else '')}


def owned_photo(db, identity, photo_id):
    conversation(db, identity)
    row = db.scalar(select(Photo).where(Photo.conversation_id == identity, Photo.id == photo_id))
    if row is None:
        raise HTTPException(404, 'Photo not found')
    from vendoo_studio.services.hosted_photos import check_key
    check_key(row.stored_filename, db.user_id, identity)
    return row


@router.get('/conversations/{identity}/photos', response_model=list[contract.ListingPhoto], responses=NOT_FOUND)
def photos(identity: str, marketplace: str | None = None, db=Depends(get_seller_db, scope="function")):
    conversation(db, identity)
    statement = select(Photo).where(Photo.conversation_id == identity)
    statement = statement.where(Photo.marketplace == marketplace) if marketplace else statement.where(Photo.marketplace.is_(None))
    return [photo_response(row) for row in db.scalars(statement.order_by(Photo.display_order, Photo.id)).all()]


@router.get('/conversations/{identity}/photos/{photo_id}/bytes')
def photo_bytes(identity: str, photo_id: str, request: Request, db=Depends(get_seller_db, scope="function")):
    from vendoo_studio.services.hosted_photos import storage_call
    row = owned_photo(db, identity, photo_id)
    key = row.stored_filename
    # Ownership was verified above. Storage can be slow, so release this read
    # transaction before its network I/O; retain only the checked object key.
    db.rollback()
    data = storage_call('GET', key, request.headers['authorization'].partition(' ')[2])
    return Response(data, media_type='image/jpeg')


@router.get('/photos/{photo_id}')
def photo_by_id(photo_id: str, request: Request, db=Depends(get_seller_db, scope="function")):
    row = db.get(Photo, photo_id)
    if row is None:
        raise HTTPException(404, 'Photo not found')
    return photo_bytes(row.conversation_id, photo_id, request, db)


@router.delete('/conversations/{identity}/photos/{photo_id}')
def delete_photo(identity: str, photo_id: str, request: Request, db=Depends(get_seller_db, scope="function")):
    from vendoo_studio.services.hosted_photos import check_key, storage_call
    conversation(db, identity, lock=True)
    assert_idle(db, identity)
    row = owned_photo(db, identity, photo_id)
    keys = {row.stored_filename}
    if row.source_filename:
        check_key(row.source_filename, db.user_id, identity)
        keys.add(row.source_filename)
    from vendoo_studio.services.hosted_listing_contract import remove_photo_selections
    remove_photo_selections(db, identity, photo_id)
    db.delete(row)
    db.commit()
    # The durable deletion wins. A temporary Storage outage may leave a private
    # orphan for cleanup; it cannot resurrect the photo or delete another owner.
    for key in keys:
        try:
            storage_call('DELETE', key, request.headers['authorization'].partition(' ')[2])
        except HTTPException:
            continue
    return {'ok': True}


@router.patch('/conversations/{identity}/photos/order')
def order_photos(identity: str, body: list[str], db=Depends(get_seller_db, scope="function")):
    conversation(db, identity, lock=True)
    assert_idle(db, identity)
    if len(body) > 30:
        raise HTTPException(422, 'Too many photos')
    rows = db.scalars(select(Photo).where(Photo.conversation_id == identity, Photo.marketplace.is_(None))).all()
    if len(set(body)) != len(body) or set(body) != {row.id for row in rows}:
        raise HTTPException(422, 'Choose each photo from this listing exactly once')
    positions = {photo_id: index for index, photo_id in enumerate(body)}
    for row in rows:
        row.display_order = positions[row.id]
    db.commit()
    return {'ok': True}


@router.get('/marketplace-media')
def media_rules():
    from vendoo_studio.services.marketplace_media import rules
    return rules()



@router.get('/photos/{photo_id}/thumb')
def photo_thumbnail(photo_id: str, request: Request, size: int = Query(192, ge=32, le=512), v: str = '',
                    db=Depends(get_seller_db, scope="function")):
    from vendoo_studio.services.hosted_photos import check_key, thumbnail
    # One row: row security limits it to the seller, and the key must sit under their draft.
    row = db.execute(select(Photo.conversation_id, Photo.stored_filename, Photo.checksum)
                     .where(Photo.id == photo_id)).first()
    if row is None:
        raise HTTPException(404, 'Photo not found')
    check_key(row.stored_filename, db.user_id, row.conversation_id)
    key, version = row.stored_filename, row.checksum
    # Release the read transaction before Storage I/O, as photo_bytes does.
    db.rollback()
    data = thumbnail(key, version, size, request.headers['authorization'].partition(' ')[2])
    # The URL names this version of the photo, so the browser keeps it for this sign-in.
    kept = bool(v) and v == (version or '')[:12]
    return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=31536000, immutable',
                                                           'Vary': 'Authorization'} if kept else None)


@router.get('/conversations/{identity}/revisions')
def revisions(identity: str, db=Depends(get_seller_db, scope="function")):
    conversation(db, identity)
    rows = db.scalars(select(ListingRevision).where(ListingRevision.conversation_id == identity)
                      .order_by(ListingRevision.created_at.desc(), ListingRevision.id.desc())).all()
    ids = {row.id for row in rows}
    return [{'id': row.id, 'source': row.source, 'created_at': row.created_at,
             'parent_revision_id': row.parent_revision_id if row.parent_revision_id in ids else None,
             'title': (row.listing_json or {}).get('title', '')} for row in rows]


def owned_revision(db, identity, revision_id):
    conversation(db, identity)
    row = db.scalar(select(ListingRevision).where(ListingRevision.id == revision_id,
                                                 ListingRevision.conversation_id == identity))
    if row is None:
        raise HTTPException(404, 'Revision not found')
    return row


@router.get('/conversations/{identity}/revisions/{revision_id}')
def get_revision(identity: str, revision_id: str, db=Depends(get_seller_db, scope="function")):
    row = owned_revision(db, identity, revision_id)
    return {'id': row.id, 'listing': stored_listing_fields(row.listing_json)}


class RestoreInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision_id: str = Field(max_length=100)


@router.post('/conversations/{identity}/revisions/{revision_id}/restore')
def restore_revision(identity: str, revision_id: str, body: RestoreInput, db=Depends(get_seller_db, scope="function")):
    earlier = get_revision(identity, revision_id, db)
    result = save_listing(identity, contract.ListingSave(listing=earlier['listing'], expected_revision_id=body.expected_revision_id), db)
    return {'ok': True, 'revision_id': result['revision_id']}


@router.delete('/conversations/{identity}', response_model=contract.DeleteConversationResult, responses={**REFUSED, **NOT_FOUND, **CONFLICT})
def delete_conversation(identity: str, request: Request, db=Depends(get_seller_db, scope="function")):
    from vendoo_studio.services import hosted_listing_delete
    conversation(db, identity, lock=True)
    assert_idle(db, identity)
    photos = db.scalar(select(func.count()).select_from(Photo).where(Photo.conversation_id == identity))
    try:
        keys = hosted_listing_delete.remove_unsent_draft(db, identity)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    db.commit()
    hosted_listing_delete.release(keys, request.headers['authorization'].partition(' ')[2])
    return {'ok': True, 'deleted_jobs': 0, 'deleted_photos': photos}


@router.post('/conversations/{identity}/discard-blank')
def discard_blank_conversation(identity: str, request: Request, db=Depends(get_seller_db, scope="function")):
    """Delete a new listing the seller left without adding anything; keep it otherwise."""
    from vendoo_studio.services import blank_drafts, hosted_listing_delete
    row = conversation(db, identity, lock=True)
    if not blank_drafts.is_blank(db, row):
        return {'discarded': False}
    try:
        keys = hosted_listing_delete.remove_unsent_draft(db, identity)
    except ValueError:
        # Something is running on it, so it is not untouched.
        db.rollback()
        return {'discarded': False}
    db.commit()
    hosted_listing_delete.release(keys, request.headers['authorization'].partition(' ')[2])
    return {'discarded': True}


class FormRefreshInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision_id: str = Field(min_length=1, max_length=100)


@router.post('/conversations/{identity}/native-marketplaces/{marketplace}/form/refresh')
def refresh_native_catalog_form(identity: str, marketplace: Literal['poshmark', 'depop', 'mercari', 'ebay', 'etsy'],
                                body: FormRefreshInput, db=Depends(get_seller_db, scope="function")):
    from vendoo_studio.services.native_editor import refresh_form
    from vendoo_studio.services.native_marketplaces import NativeMarketplaceError

    try:
        return refresh_form(db, identity, marketplace, body.revision_id)
    except NativeMarketplaceError as error:
        raise HTTPException(error.status_code, str(error)) from None


@router.get('/conversations/{identity}/native-marketplaces/{marketplace}/form')
def native_catalog_form(identity: str, marketplace: Literal['poshmark', 'depop', 'mercari', 'ebay', 'etsy'],
                        revision_id: str | None = None, db=Depends(get_seller_db, scope="function")):
    from vendoo_studio.services.choice_lists import share
    from vendoo_studio.services.native_editor import read_form
    from vendoo_studio.services.native_marketplaces import NativeMarketplaceError

    # These canonical builders read verified catalogs and the owned snapshot.
    # Mercari also reads anonymous public shipping quotes, and eBay reads its
    # category schema and the seller's own policies with their connected
    # account. No marketplace write runs. A plain function runs in FastAPI's
    # threadpool, so none of it waits on the request loop.
    try:
        current = db.get(Listing, identity)
        if current and current.current_revision_id and hosted_drafts.current_fields_revision(db, identity) is None:
            raise HTTPException(409, 'This listing needs its revision link repaired before editing.')
        return share(read_form(db, identity, marketplace, revision_id))
    except NativeMarketplaceError as error:
        raise HTTPException(error.status_code, str(error)) from None
