"""Evaluate the seller row-security predicate once per query, not once per row.

A bare current_setting(...)::jsonb in a policy is re-evaluated, JSON parse
included, for every row a scan reads. Wrapped in a scalar subquery, Postgres
evaluates it once per statement (an InitPlan), as Supabase's row-security
guide recommends. The predicate means the same thing, and the policies stay
RESTRICTIVE (ALTER POLICY never changes that).

Revision ID: 5d2a8c71e4f9
Revises: b7e912ca4801
"""
from alembic import op
import sqlalchemy as sa

revision = '5d2a8c71e4f9'
down_revision = 'b7e912ca4801'
branch_labels = None
depends_on = None

CLAIMED_SELLER = "nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'"
LOCAL_OWNER = "'00000000-0000-0000-0000-000000000000'"
ONCE = f"user_id = (SELECT {CLAIMED_SELLER}) AND user_id <> {LOCAL_OWNER}"
PER_ROW = f"user_id = ({CLAIMED_SELLER}) AND user_id <> {LOCAL_OWNER}"


def _apply(owner: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    policies = bind.execute(sa.text("""
        SELECT relation.relname, policy.polname
        FROM pg_policy AS policy
        JOIN pg_class AS relation ON relation.oid = policy.polrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public' AND policy.polname IN ('seller_owner', 'seller_subscriber')
        ORDER BY relation.relname, policy.polname
    """)).all()
    # ALTER POLICY takes the table's ACCESS EXCLUSIVE lock. One table per
    # transaction holds each lock for milliseconds and never waits for one
    # table while holding another. Either predicate is correct, so a deploy
    # that stops part way leaves every table protected, and rerunning is safe.
    with op.get_context().autocommit_block():
        for table, name in policies:
            if name == 'seller_owner':
                op.execute(f'ALTER POLICY seller_owner ON "{table}" USING ({owner}) WITH CHECK ({owner})')
            else:
                # Supabase Realtime's read-only feed policy (seller_changes).
                op.execute(f'ALTER POLICY seller_subscriber ON "{table}" USING ({owner})')


def upgrade() -> None:
    _apply(ONCE)


def downgrade() -> None:
    _apply(PER_ROW)
