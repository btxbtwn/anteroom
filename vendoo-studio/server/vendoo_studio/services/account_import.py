"""Copy a Mac's records into the signed-in seller's account.

The Mac sends its rows table by table, parents first. Every row is written
under the verified seller (never the Mac's local owner), through the seller
session, so row security checks each insert. Rows already in the account are
left alone, which lets an interrupted move run again without duplicates.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Date, DateTime, Table, and_, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from vendoo_studio.database import Base, load_models
from vendoo_studio.models.job import ACTIVE_JOB_STATUSES

# Not copied: the server's own task queue, secret references (secrets move to
# Vault separately), videos (the web app has none), and photo fingerprints,
# which are rebuilt from the converted photos.
SKIPPED_TABLES = frozenset({
    'background_tasks', 'background_task_events', 'user_secret_references',
    'listing_videos', 'photo_fingerprints',
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
    row = {name: _value(table.c[name], value) for name, value in raw.items() if name != 'user_id'}
    row['user_id'] = user_id
    if table.name == 'jobs' and row.get('status') in ACTIVE_JOB_STATUSES:
        # Mac-side work cannot continue in the account; it stops, never retries.
        row['status'] = 'cancelled'
    return row


def _key(table: Table, row: dict) -> tuple:
    return tuple(row.get(column.name) for column in table.primary_key.columns)


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
    db.commit()
    return {'inserted': len(inserted_keys), 'existing': existing}


def pending_photos(db) -> list[dict]:
    """Photos whose files have not been copied into the account's storage yet."""
    from vendoo_studio.models.conversation import Photo
    prefix = db.user_id + '/'
    rows = db.execute(select(Photo.id, Photo.stored_filename, Photo.source_filename).where(or_(
        ~Photo.stored_filename.startswith(prefix),
        and_(Photo.source_filename.is_not(None), ~Photo.source_filename.startswith(prefix)),
    )).order_by(Photo.id)).all()
    return [{'id': row.id,
             'fields': [field for field, value in (('stored', row.stored_filename), ('source', row.source_filename))
                        if value is not None and not value.startswith(prefix)]}
            for row in rows]


def import_photo(db, photo_id: str, field: str, data: bytes, token: str) -> dict:
    """Convert one Mac photo file to the account's JPEG format and store it privately."""
    import hashlib

    from vendoo_studio.models.conversation import Photo
    from vendoo_studio.services import hosted_photos
    if field not in {'stored', 'source'}:
        raise HTTPException(422, 'Choose the photo or its original')
    photo = db.scalar(select(Photo).where(Photo.id == photo_id).with_for_update())
    if photo is None:
        raise HTTPException(404, 'Photo not found')
    attribute = 'stored_filename' if field == 'stored' else 'source_filename'
    current = getattr(photo, attribute)
    if current is None or current.startswith(db.user_id + '/'):
        return {'copied': False}
    data, width, height = hosted_photos.normalize(data)
    key = hosted_photos.new_key(db.user_id, photo.conversation_id)
    setattr(photo, attribute, key)
    if field == 'stored':
        photo.mime_type, photo.size_bytes = 'image/jpeg', len(data)
        photo.checksum, photo.width, photo.height = hashlib.sha256(data).hexdigest(), width, height
    db.flush()
    hosted_photos.storage_call('POST', key, token, data)
    try:
        db.commit()
    except Exception:
        db.rollback()
        try:
            hosted_photos.storage_call('DELETE', key, token)
        except HTTPException:
            pass
        raise
    return {'copied': True}


def import_settings(values: dict) -> list[str]:
    """Fill in settings the account does not have yet; the account's own choices win."""
    from vendoo_studio.services.user_settings import update_settings
    added: list[str] = []

    def fill(payload: dict) -> None:
        for name, value in values.items():
            if name not in payload:
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
    import json

    from vendoo_studio.services import keychain
    if not isinstance(values, dict) or set(values) - SECRET_SLOTS:
        raise HTTPException(422, 'Only provider keys and sign-ins can be moved')
    if any(not isinstance(value, str) or not value or len(value) > MAX_SECRET_CHARS for value in values.values()):
        raise HTTPException(422, 'Check the keys and try again')
    saved: list[str] = []
    for slot, value in values.items():
        existing = keychain.read_slot(slot)
        if slot == 'ai-api-keys':
            try:
                incoming = json.loads(value)
                current = json.loads(existing) if existing else {}
            except ValueError:
                raise HTTPException(422, 'Check the keys and try again') from None
            if not isinstance(incoming, dict) or not isinstance(current, dict):
                raise HTTPException(422, 'Check the keys and try again')
            merged = {**incoming, **current}
            if merged != current:
                keychain.write_slot(slot, json.dumps(merged))
                saved.append(slot)
        elif not existing:
            keychain.write_slot(slot, value)
            saved.append(slot)
    return saved


def announce_all(db) -> None:
    from vendoo_studio.models.seller_change import TOPIC_BY_TABLE
    from vendoo_studio.services.hosted_database import note_changes
    db.execute(select(1))  # begin, so the commit publishes
    note_changes(db, *(table for table, topic in TOPIC_BY_TABLE.items() if topic))
    db.commit()
