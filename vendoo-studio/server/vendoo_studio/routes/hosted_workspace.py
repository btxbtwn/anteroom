"""The existing application's account-scoped HTTP contract.

Every query uses the verified seller session. No desktop services are imported
or started, and no request accepts an owner, file path or operational identity.
"""
import asyncio
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
from vendoo_studio.models.marketplace_listing import LIVE_STATES, MarketplaceListing
from vendoo_studio.models.validation import etsy_eligible, validate_listing
from vendoo_studio.repositories import hosted_drafts
from vendoo_studio.services import user_settings
from vendoo_studio.services.hosted_database import get_seller_db
from vendoo_studio.services.hosted_listing_contract import editable_listing
from vendoo_studio.version import app_version

router = APIRouter(prefix='/api')


def conversation(db, identity, *, lock=False):
    statement = select(Conversation).where(Conversation.id == identity)
    row = db.scalar(statement.with_for_update() if lock else statement)
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


def conversation_response(row, listing=None, direct=None, *, include_notes=True):
    result = {'id': row.id, 'title': row.title, 'status': row.status,
            'box_id': row.box_id, 'settled_at': row.settled_at, 'unsettled_at': row.unsettled_at,
            'created_at': row.created_at, 'updated_at': row.updated_at,
            'sku': (listing or {}).get('sku'), 'price': (listing or {}).get('price'),
            'quantity': (listing or {}).get('quantity'), 'direct_marketplaces': direct or []}
    if include_notes:
        result['notes'] = row.notes
    return result


def listing_response(db, identity):
    revision = hosted_drafts.current_fields_revision(db, identity)
    raw = revision.listing_json if revision and isinstance(revision.listing_json, dict) else {}
    from vendoo_studio.services.hosted_listing_contract import ROOTS
    payload = editable_listing({key: value for key, value in raw.items() if key in ROOTS})
    if 'title' not in payload:
        payload['title'] = conversation(db, identity).title or ''
    count = db.scalar(select(func.count()).select_from(Photo).where(Photo.conversation_id == identity))
    import copy
    validation = validate_listing(copy.deepcopy(payload), count, require_photos=True)
    return {'conversation_id': identity, 'current_revision_id': revision.id if revision else None,
            'listing': payload, 'revision_count': db.scalar(select(func.count()).select_from(ListingRevision)
                .where(ListingRevision.conversation_id == identity)), 'can_send': validation.can_send,
            'errors': validation.errors, 'warnings': validation.warnings, 'etsy_eligible': etsy_eligible(payload)}


@router.get('/runtime')
def runtime(request: Request, db=Depends(get_seller_db)):
    from vendoo_studio.services.category_tree import categories_ready
    worker = getattr(request.app.state, 'background_worker', None)
    generation = bool(request.app.state.background_worker_enabled and worker is not None and not worker.done())
    return {'mode': 'hosted', 'features': ['inventory', 'listing_fields', 'photos', 'preferences', 'photo_edits', 'bulk_actions', 'bulk_edit', 'bulk_copy', 'bulk_delete', 'bulk_markdowns', 'suggestions']
            + (['generation', 'chat_attachments', 'bulk_regenerate'] if generation else [])
            + (['catalog_categories'] if categories_ready(db) else []),
            'views': ['listings', 'analytics', 'settings', 'usage'], 'marketplace_forms': ['poshmark', 'depop', 'mercari', 'ebay']}


@router.get('/status')
def status(db=Depends(get_seller_db)):
    from vendoo_studio.services.listing_provider import provider_is_configured
    active = db.scalars(select(Job.id).where(Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)).first()
    return {'version': app_version(), 'provider_configured': provider_is_configured(), 'extension_connected': False,
            'active_job_id': active, 'conversations': db.scalar(select(func.count()).select_from(Conversation)),
            'database_ok': True, 'packaged': False, 'chrome_available': False, 'setup_guide_dismissed': True}


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=50000)
    box_id: str | None = Field(None, max_length=100)


@router.get('/conversations')
def list_conversations(db=Depends(get_seller_db)):
    # Keep the existing sidebar's complete-list contract; it filters and
    # virtualizes locally. No historical pointers or fallback file URLs escape.
    rows = db.scalars(select(Conversation).options(defer(Conversation.notes)).order_by(Conversation.updated_at.desc(), Conversation.id.desc())).all()
    ordered = select(Photo.conversation_id, Photo.id, Photo.checksum, func.row_number().over(
        partition_by=Photo.conversation_id, order_by=(Photo.display_order, Photo.id)).label('position')).where(
            Photo.marketplace.is_(None)).subquery()
    covers = {identity: (photo_id, checksum) for identity, photo_id, checksum in db.execute(
        select(ordered.c.conversation_id, ordered.c.id, ordered.c.checksum).where(ordered.c.position == 1))}
    current = {}
    for identity, sku, price, quantity in db.execute(select(Listing.conversation_id,
            json_value(ListingRevision.listing_json, 'sku'), json_value(ListingRevision.listing_json, 'price'),
            json_value(ListingRevision.listing_json, 'quantity')).join(
            ListingRevision, (ListingRevision.id == Listing.current_revision_id)
            & (ListingRevision.conversation_id == Listing.conversation_id)
            & (ListingRevision.user_id == Listing.user_id))):
        current[identity] = {'sku': sku, 'price': float(price) if price is not None else None,
                             'quantity': int(quantity) if quantity is not None else None}
    direct = {}
    for identity, market in db.execute(select(MarketplaceListing.conversation_id, MarketplaceListing.marketplace)
                                      .where(MarketplaceListing.state.in_(LIVE_STATES))):
        direct.setdefault(identity, []).append(market)
    summaries = []
    for row in rows:
        summary = conversation_response(row, current.get(row.id), direct.get(row.id), include_notes=False)
        if row.id in covers:
            from vendoo_studio.services.photos import cover_thumb_url
            summary['cover_photo_url'] = cover_thumb_url(*covers[row.id])
        summaries.append(summary)
    return summaries


@router.post('/conversations')
def create_conversation(body: ConversationInput, db=Depends(get_seller_db)):
    check_box(db, body.box_id)
    row = Conversation(title=body.title or 'New Listing', notes=body.notes or '', box_id=body.box_id, status='draft')
    db.add(row)
    db.commit()
    db.refresh(row)
    return conversation_response(row)


@router.get('/conversations/{identity}')
def get_conversation(identity: str, db=Depends(get_seller_db)):
    return conversation_response(conversation(db, identity))


@router.patch('/conversations/{identity}')
def edit_conversation(identity: str, body: ConversationInput, db=Depends(get_seller_db)):
    row = conversation(db, identity, lock=True)
    assert_idle(db, identity)
    if "box_id" in body.model_fields_set:
        check_box(db, body.box_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return conversation_response(row)


@router.get('/conversations/{identity}/listing')
def get_listing(identity: str, db=Depends(get_seller_db)):
    conversation(db, identity)
    return listing_response(db, identity)


class ListingInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    listing: dict
    expected_revision_id: str = Field(max_length=100)


@router.put('/conversations/{identity}/listing')
def save_listing(identity: str, body: ListingInput, db=Depends(get_seller_db)):
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
def messages(identity: str, db=Depends(get_seller_db)):
    conversation(db, identity)
    rows = db.scalars(select(Message).where(Message.conversation_id == identity).order_by(Message.created_at, Message.id)).all()
    return [{'id': row.id, 'conversation_id': identity, 'role': row.role, 'text': row.text,
             'provider': row.provider, 'model': row.model, 'created_at': row.created_at,
             'attachments': row.attachments or []} for row in rows]


@router.get('/conversations/{identity}/activity')
def activity(identity: str, db=Depends(get_seller_db)):
    conversation(db, identity)
    jobs = db.scalars(select(Job).where(Job.conversation_id == identity, Job.status.in_(ACTIVE_JOB_STATUSES))).all()
    count = db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == identity))
    last = db.scalar(select(Message.id).where(Message.conversation_id == identity).order_by(Message.created_at.desc()).limit(1))
    from vendoo_studio.models.background_task import BackgroundTask
    tasks = db.scalars(select(BackgroundTask).where(
        BackgroundTask.conversation_id == identity, BackgroundTask.status.in_(('queued', 'running')))).all()
    labels = [f'{row.kind}: {row.status}' for row in tasks]
    return {'busy': bool(jobs or tasks), 'items': [row.current_step or row.status for row in jobs] + labels,
            'message_count': count, 'last_message_id': last, 'agents': []}


class UiInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    theme: Literal['dark', 'light', 'system'] | None = None
    listing_names: Literal['title', 'sku'] | None = None
    settled_shelf_expanded: bool | None = None
    stale_listing_days: int | None = Field(None, ge=15, le=120)
    recent_vendoo_labels: list[str] | None = Field(None, max_length=12)
    remember_labels: str | list[str] | None = None
    restore_labels: list[str] | None = None
    forget_label: str | None = None
    model_favorites: list[dict] | None = None
    label_colors: dict[str, Literal["gray", "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink"]] | None = Field(None, max_length=200)
    vendoo_enabled: Literal[False] | None = None


@router.get('/settings/ui')
def ui(db=Depends(get_seller_db)):
    return {'ok': True, **user_settings.get_ui_prefs(), 'vendoo_enabled': False}


@router.put('/settings/ui')
def save_ui(body: UiInput, db=Depends(get_seller_db)):
    from vendoo_studio.routes.settings import UiPrefsConfig, put_ui_prefs
    put_ui_prefs(UiPrefsConfig(**body.model_dump(exclude_unset=True, exclude_none=True)))
    return ui(db)



def job_response(row):
    return {key: getattr(row, key) for key in ('id', 'conversation_id', 'approved_revision_id', 'status',
        'current_step', 'attempt_count', 'last_error', 'created_at', 'updated_at')}


@router.get('/jobs')
def jobs(conversation_id: str | None = None, db=Depends(get_seller_db)):
    statement = select(Job)
    if conversation_id:
        conversation(db, conversation_id)
        statement = statement.where(Job.conversation_id == conversation_id)
    return [job_response(row) for row in db.scalars(statement.order_by(Job.created_at.desc()).limit(100)).all()]


@router.get('/jobs/queue')
def queue(db=Depends(get_seller_db)):
    rows = db.scalars(select(Job).where(Job.status.in_(ACTIVE_JOB_STATUSES)).order_by(Job.created_at)).all()
    return {'work': [], 'jobs': [job_response(row) for row in rows]}


class ResetInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    keep_inputs: bool = False


@router.post('/conversations/{identity}/reset')
def reset_conversation(identity: str, request: Request, body: ResetInput | None = None, db=Depends(get_seller_db)):
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


@router.get('/conversations/{identity}/native-marketplaces')
def native_status(identity: str, db=Depends(get_seller_db)):
    conversation(db, identity)
    rows = db.scalars(select(MarketplaceListing).where(MarketplaceListing.conversation_id == identity)).all()
    return {row.marketplace: {'state': row.state, 'listing_url': row.listing_url} for row in rows}



def photo_response(row):
    return {'id': row.id, 'conversation_id': row.conversation_id, 'original_filename': row.original_filename,
            'mime_type': row.mime_type, 'size_bytes': row.size_bytes, 'display_order': row.display_order,
            'width': row.width, 'height': row.height, 'created_at': row.created_at, 'edits': row.edits,
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


@router.get('/conversations/{identity}/photos')
def photos(identity: str, marketplace: str | None = None, db=Depends(get_seller_db)):
    conversation(db, identity)
    statement = select(Photo).where(Photo.conversation_id == identity)
    statement = statement.where(Photo.marketplace == marketplace) if marketplace else statement.where(Photo.marketplace.is_(None))
    return [photo_response(row) for row in db.scalars(statement.order_by(Photo.display_order, Photo.id)).all()]


@router.get('/conversations/{identity}/photos/{photo_id}/bytes')
def photo_bytes(identity: str, photo_id: str, request: Request, db=Depends(get_seller_db)):
    from vendoo_studio.services.hosted_photos import storage_call
    row = owned_photo(db, identity, photo_id)
    data = storage_call('GET', row.stored_filename, request.headers['authorization'].partition(' ')[2])
    return Response(data, media_type='image/jpeg')


@router.get('/photos/{photo_id}')
def photo_by_id(photo_id: str, request: Request, db=Depends(get_seller_db)):
    row = db.get(Photo, photo_id)
    if row is None:
        raise HTTPException(404, 'Photo not found')
    return photo_bytes(row.conversation_id, photo_id, request, db)


@router.delete('/conversations/{identity}/photos/{photo_id}')
def delete_photo(identity: str, photo_id: str, request: Request, db=Depends(get_seller_db)):
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
def order_photos(identity: str, body: list[str], db=Depends(get_seller_db)):
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
def photo_thumbnail(photo_id: str, request: Request, size: int = Query(192, ge=32, le=512), db=Depends(get_seller_db)):
    row = db.scalar(select(Photo).where(Photo.id == photo_id))
    if row is None:
        raise HTTPException(404, 'Photo not found')
    response = photo_bytes(row.conversation_id, photo_id, request, db)
    from io import BytesIO
    from PIL import Image
    with Image.open(BytesIO(response.body)) as image:
        image.thumbnail((size, size))
        output = BytesIO()
        image.save(output, 'JPEG', quality=82)
    return Response(output.getvalue(), media_type='image/jpeg')


@router.get('/conversations/{identity}/marketplace-listings')
def marketplace_listings(identity: str, db=Depends(get_seller_db)):
    conversation(db, identity)
    rows = db.scalars(select(MarketplaceListing).where(MarketplaceListing.conversation_id == identity)).all()
    return {row.marketplace: {'state': row.state, 'listing_url': row.listing_url,
                             'listing_id': row.listing_id} for row in rows}


@router.get('/conversations/{identity}/revisions')
def revisions(identity: str, db=Depends(get_seller_db)):
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
def get_revision(identity: str, revision_id: str, db=Depends(get_seller_db)):
    row = owned_revision(db, identity, revision_id)
    from vendoo_studio.services.hosted_listing_contract import ROOTS
    return {'id': row.id, 'listing': {key: value for key, value in row.listing_json.items() if key in ROOTS}}


class RestoreInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision_id: str = Field(max_length=100)


@router.post('/conversations/{identity}/revisions/{revision_id}/restore')
def restore_revision(identity: str, revision_id: str, body: RestoreInput, db=Depends(get_seller_db)):
    earlier = get_revision(identity, revision_id, db)
    result = save_listing(identity, ListingInput(listing=earlier['listing'], expected_revision_id=body.expected_revision_id), db)
    return {'ok': True, 'revision_id': result['revision_id']}


@router.delete('/conversations/{identity}')
def delete_conversation(identity: str, request: Request, db=Depends(get_seller_db)):
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


@router.get('/conversations/{identity}/native-marketplaces/{marketplace}/form')
async def native_catalog_form(identity: str, marketplace: Literal['poshmark', 'depop', 'mercari', 'ebay'],
                              revision_id: str | None = None, db=Depends(get_seller_db)):
    from starlette.concurrency import run_in_threadpool
    from vendoo_studio.services.native_editor import editor_form
    from vendoo_studio.services.native_marketplaces import NativeMarketplaceError

    def load():
        # These canonical builders read verified catalogs and the owned
        # snapshot. Mercari also reads anonymous public shipping quotes, and
        # eBay reads its category schema and the seller's own policies with
        # their connected account. No browser or marketplace write runs.
        try:
            current = db.get(Listing, identity)
            if current and current.current_revision_id and hosted_drafts.current_fields_revision(db, identity) is None:
                raise HTTPException(409, 'This listing needs its revision link repaired before editing.')
            return asyncio.run(editor_form(identity, marketplace, revision_id, db))
        except NativeMarketplaceError as error:
            raise HTTPException(error.status_code, str(error)) from None
    return await run_in_threadpool(load)
