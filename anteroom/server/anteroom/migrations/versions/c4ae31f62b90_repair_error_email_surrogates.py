"""Repair unpaired surrogates in the Postgres operator mail outbox.

Revision ID: c4ae31f62b90
Revises: c7a3e9f41b28
"""

import json
import re

import sqlalchemy as sa
from alembic import op

revision = 'c4ae31f62b90'
down_revision = 'c7a3e9f41b28'
branch_labels = None
depends_on = None

_SURROGATE = re.compile(r'[\ud800-\udfff]')
_SURROGATE_ESCAPE = r'\\u[dD][89a-fA-F][0-9a-fA-F]{2}'


def _repaired(payload):
    # The JSON decoder already combined valid pairs. The dump leaves Unicode
    # as-is, so a literal backslash escape in the report is left alone.
    data = json.dumps(payload, ensure_ascii=False)
    repaired = _SURROGATE.sub('\ufffd', data)
    return json.loads(repaired) if repaired != data else None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != 'postgresql':
        return

    state = sa.table('error_email_state', sa.column('id', sa.Integer), sa.column('payload', sa.JSON),
                     sa.column('group_sync_version', sa.Integer))
    groups = sa.table('error_email_groups', sa.column('id', sa.Integer), sa.column('payload', sa.JSON))
    # Read only candidates. As in the application, take the control-row lock
    # before modifying any group or its mirror.
    current = connection.scalar(sa.select(state.c.payload).where(
        state.c.id == 1, sa.cast(state.c.payload, sa.Text).regexp_match(_SURROGATE_ESCAPE)).with_for_update())
    repaired = _repaired(current)
    if repaired is None:
        return

    for group_id, payload in connection.execute(sa.select(groups.c.id, groups.c.payload).where(
        sa.cast(groups.c.payload, sa.Text).regexp_match(_SURROGATE_ESCAPE),
    )).all():
        if (fixed := _repaired(payload)) is not None:
            connection.execute(groups.update().where(groups.c.id == group_id).values(payload=fixed))
    connection.execute(state.update().where(state.c.id == 1).values(
        payload=repaired, group_sync_version=state.c.group_sync_version + 1))


def downgrade() -> None:
    pass  # Repaired text remains valid on older versions.
