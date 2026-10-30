"""Account settings in Postgres; no shared filesystem or plaintext credentials."""
from sqlalchemy import cast, JSON, select
from sqlalchemy.dialects.postgresql import JSONB, insert

from vendoo_studio.models.user_preference import UserPreference


def read(db, key):
    value = db.scalar(select(UserPreference.value).where(UserPreference.key == key))
    return value if isinstance(value, dict) else {}


def patch(db, key, values):
    statement = insert(UserPreference).values(user_id=db.user_id, key=key, value=values)
    # Merge on the server so simultaneous theme/label saves cannot lose one
    # another, including the first two writes to a new account's preference row.
    merged = cast(cast(UserPreference.value, JSONB).op('||')(cast(statement.excluded.value, JSONB)), JSON)
    db.execute(statement.on_conflict_do_update(index_elements=['user_id', 'key'], set_={'value': merged}))
    db.commit()
    return read(db, key)
