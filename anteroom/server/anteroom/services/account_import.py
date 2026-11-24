"""Copy a Mac's records into the signed-in seller's account.

The Mac sends its rows table by table, parents first. Every row is written
under the verified seller (never the Mac's local owner), through the seller
session, so row security checks each insert. Rows already in the account
keep their data. For the same Vendoo item, an absent label field can be filled
from the Mac's saved labels. An interrupted move can run again without duplicates.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Date, DateTime, Table, and_, case, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from anteroom.database import Base, load_models
from anteroom.models.job import ACTIVE_JOB_STATUSES

# Not copied: the server's own task queue, secret references (secrets move to
# Vault separately), videos (the web app has none), and photo fingerprints,
# which are rebuilt from the converted photos. Feedback an assistant sent from
# the Mac stays there with whatever delivery note it already has.
SKIPPED_TABLES = frozenset({
    'background_tasks', 'background_task_events', 'user_secret_references',
    'cloud_marketplace_sessions', 'cloud_session_audit', 'cloud_operation_journals',
    'mcp_access_tokens', 'mcp_oauth_connections', 'mcp_feedback',
    'listing_videos', 'photo_fingerprints',
    'plaid_connections', 'plaid_transactions',
})
MAX_ROWS = 500


def seller_tables() -> list[Table]:
    load_models()
    return [table for table in Base.metadata.sorted_tables if 'user_id' in table.c]


def imported_tables() -> list[Table]:
    """Every seller table except the skipped ones, parents before children."""
    return [table for table in seller_tables() if table.name not in SKIPPED_TABLES]


def _table(name: str) -> Table:
    for table in imported_tables():
        if table.name == name:
            return table
    raise HTTPException(422, 'This kind of record cannot be moved')


def _value(column, value: Any) -> Any:
    if value is None or not isinstance(value, str):
        return value
    try:
        if isinstance(column.type, DateTime):
            parsed = datetime.fromisoformat(value)
            # Columns hold naive UTC, as the Mac wrote them.
            return parsed if parsed.tzinfo is None else parsed.astimezone(UTC).replace(tzinfo=None)
        if isinstance(column.type, Date):
            return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, 'A record has a date this account cannot read') from None
    return value


def _row(table: Table, raw: dict, user_id: str) -> dict:
    unknown = set(raw) - set(table.c.keys())
    if unknown:
        # A newer Mac build than this server: refuse rather than drop fields.
        raise HTTPException(409, 'This Mac has a newer version of Anteroom than your account. Try again after the next account update.')
    # Generated inventory fields come from listing_json on this database.
    row = {name: _value(table.c[name], value) for name, value in raw.items()
           if name != 'user_id' and table.c[name].computed is None}
    row['user_id'] = user_id
    if table.name == 'jobs' and row.get('status') in ACTIVE_JOB_STATUSES:
        # Mac-side work cannot continue in the account; it stops, never retries.
        row['status'] = 'cancelled'
    return row


def _key(table: Table, row: dict) -> tuple:
    return tuple(row.get(column.name) for column in table.primary_key.columns)


def _saved_notes(raw: Any) -> dict:
    """Read the stored keys without normalizing other Vendoo metadata."""
    if not isinstance(raw, str):
        return {}
    try:
        notes = json.loads(raw)
    except ValueError:
        return {}
    return notes if isinstance(notes, dict) else {}


def _restore_labels(db, table: Table, rows: list[dict], skipped: set[tuple]) -> None:
    incoming = {}
    for row in rows:
        if (row["id"],) not in skipped:
            continue
        notes = _saved_notes(row.get("notes"))
        item_id = notes.get("vendooItemId")
        labels = notes.get("vendooLabels")
        if isinstance(item_id, str) and item_id.strip() and isinstance(labels, str) and labels.strip():
            incoming[row["id"]] = (item_id, labels)
    if not incoming:
        return

    # Serialize with Notes & Labels edits. Read the saved notes under the lock,
    # including a deliberately empty label field.
    owned = table.c.user_id == db.user_id
    current = db.execute(
        select(table.c.id, table.c.notes).where(owned, table.c.id.in_(incoming))
        .order_by(table.c.id).with_for_update()
    )
    restored = {}
    for item, raw in current:
        notes = _saved_notes(raw)
        item_id, labels = incoming[item]
        if notes.get("vendooItemId") == item_id and "vendooLabels" not in notes:
            restored[item] = json.dumps({**notes, "vendooLabels": labels})
    if restored:
        db.execute(
            update(table).where(owned, table.c.id.in_(restored))
            .values(notes=case(restored, value=table.c.id), updated_at=table.c.updated_at)
        )


def import_rows(db, table_name: str, rows: list[dict]) -> dict:
    table = _table(table_name)
    if not isinstance(rows, list) or len(rows) > MAX_ROWS or not all(isinstance(row, dict) for row in rows):
        raise HTTPException(422, f'Send at most {MAX_ROWS} records at a time')
    if not rows:
        return {'inserted': 0, 'existing': 0}
    prepared = [_row(table, raw, db.user_id) for raw in rows]
    keys = list(table.primary_key.columns)
    if any(value is None for row in prepared for value in _key(table, row)):
        raise HTTPException(422, 'Each record needs its id')
    inserted = db.execute(insert(table).values(prepared).on_conflict_do_nothing().returning(*keys)).all()
    inserted_keys = {tuple(row) for row in inserted}
    skipped = [_key(table, row) for row in prepared if _key(table, row) not in inserted_keys]
    existing = 0
    if skipped:
        match = tuple_(*keys).in_(skipped) if len(keys) > 1 else keys[0].in_([key[0] for key in skipped])
        existing = len(db.execute(select(*keys).where(match)).all())
    if existing != len(skipped):
        # Ids are unique across all accounts: these rows already belong to
        # someone else, most likely because this Mac moved to another account.
        db.rollback()
        raise HTTPException(409, 'Some of this Mac’s listings are already in a different account. Sign in to that account to move them.')
    if table_name == 'conversations' and skipped:
        _restore_labels(db, table, prepared, set(skipped))
    db.commit()
    return {'inserted': len(inserted_keys), 'existing': existing}


def pending_photos(db) -> list[dict]:
    """Photos whose files have not been copied into the account's storage yet."""
    from anteroom.models.conversation import Photo
    prefix = db.user_id + '/'
    rows = db.execute(select(Photo.id, Photo.stored_filename, Photo.source_filename).where(or_(
        ~Photo.stored_filename.startswith(prefix),
        and_(Photo.source_filename.is_not(None), ~Photo.source_filename.startswith(prefix)),
    )).order_by(Photo.id)).all()
    return [{'id': row.id,
             'fields': [field for field, value in (('stored', row.stored_filename), ('source', row.source_filename))
                        if value is not None and not value.startswith(prefix)]}
            for row in rows]


def _check_field(field: str) -> str:
    if field not in {'stored', 'source'}:
        raise HTTPException(422, 'Choose the photo or its original')
    return 'stored_filename' if field == 'stored' else 'source_filename'


def photo_to_import(db, photo_id: str, field: str) -> str | None:
    """The listing a Mac photo file belongs to, or None when the account already has the file."""
    from anteroom.models.conversation import Photo
    attribute = _check_field(field)
    photo = db.get(Photo, photo_id)
    if photo is None:
        raise HTTPException(404, 'Photo not found')
    current = getattr(photo, attribute)
    if current is None or current.startswith(db.user_id + '/'):
        return None
    return photo.conversation_id


def save_imported_photo(db, photo_id: str, field: str, key: str, data: bytes, width: int, height: int) -> bool:
    """Point the photo at its uploaded copy. False when another upload saved it first."""
    import hashlib

    from anteroom.models.conversation import Photo
    attribute = _check_field(field)
    photo = db.scalar(select(Photo).where(Photo.id == photo_id).with_for_update())
    if photo is None:
        raise HTTPException(404, 'Photo not found')
    current = getattr(photo, attribute)
    if current is None or current.startswith(db.user_id + '/'):
        return False
    setattr(photo, attribute, key)
    if field == 'stored':
        photo.mime_type, photo.size_bytes = 'image/jpeg', len(data)
        photo.checksum, photo.width, photo.height = hashlib.sha256(data).hexdigest(), width, height
    db.commit()
    return True


# Known top-level seller settings persisted via user_settings. Mac-only keys
# (such as backup_folder) and unknown keys are dropped on import.
ALLOWED_SETTINGS_KEYS = frozenset({
    'setup_guide_dismissed',
    'listing_provider',
    'photo_provider',
    'ui',
    'cursor_models',
    'claude_models',
    'key_provider_models',
    'package_dimensions_in',
    'ships_from_zip',
    'mercari_shipping_label',
    'listing_field_defaults',
    'ebay_inventory_location',
    'etsy_listing_defaults',
    'ebay_listing_defaults',
    'listing_formulas',
    'sale_detection',
    'sale_autodelist',
    'sale_auto_adjust_quantity',
    'business_note',
    'preferred_name',
    'marketplaces',
    'seller_region',
    'hidden_fields',
    'vendoo_sales_archived_at',
    'brave_quota_exhausted',
    'pricing_rules',
    'shipping_presets',
    'listing_warnings',
})


def import_settings(values: dict) -> list[str]:
    """Fill in allowlisted settings the account does not have yet; the account's own choices win."""
    from anteroom.services.user_settings import update_settings
    if not isinstance(values, dict):
        raise HTTPException(422, 'Check the settings and try again')
    added: list[str] = []

    def fill(payload: dict) -> None:
        for name, value in values.items():
            if name in ALLOWED_SETTINGS_KEYS and name not in payload:
                payload[name] = value
                added.append(name)

    update_settings(fill)
    return added


# Provider keys and the ChatGPT sign-in. eBay and Etsy are not copied: they
# reconnect in the account so sales notices reach it, and because Etsy rotates
# its refresh token, two copies would sign each other out.
SECRET_SLOTS = frozenset({
    'xiaomi-mimo-api-key', 'brave-search-api-key', 'cursor-api-key', 'ai-api-keys',
    'chatgpt-codex-oauth', 'chatgpt-registration', 'chatgpt-models',
})
MAX_SECRET_CHARS = 64 * 1024


def import_secrets(values: dict) -> list[str]:
    """Save the Mac's keys in the account's Vault, never replacing one already set there."""
    from anteroom.services.service_context import current

    if not isinstance(values, dict) or set(values) - SECRET_SLOTS:
        raise HTTPException(422, 'Only provider keys and sign-ins can be moved')
    if any(not isinstance(value, str) or not value or len(value) > MAX_SECRET_CHARS for value in values.values()):
        raise HTTPException(422, 'Check the keys and try again')
    context = current()
    if context is None:
        raise RuntimeError('Importing keys requires a seller context')

    saved: list[str] = []
    for slot, value in values.items():
        def fill(existing: str | None, slot: str = slot, value: str = value) -> str | None:
            if slot == 'ai-api-keys':
                try:
                    incoming = json.loads(value)
                    previous = json.loads(existing) if existing else {}
                except ValueError:
                    raise HTTPException(422, 'Check the keys and try again') from None
                if not isinstance(incoming, dict) or not isinstance(previous, dict):
                    raise HTTPException(422, 'Check the keys and try again')
                merged = {**incoming, **previous}
                if merged == previous:
                    return existing
                saved.append(slot)
                return json.dumps(merged)
            if not existing:
                saved.append(slot)
                return value
            return existing

        context.secrets.update(slot, fill)
    return saved


def announce_all(db) -> None:
    from anteroom.models.seller_change import TOPIC_BY_TABLE
    from anteroom.services.hosted_database import note_changes
    db.execute(select(1))  # begin, so the commit publishes
    note_changes(db, *(table for table, topic in TOPIC_BY_TABLE.items() if topic))
    db.commit()
