"""Announce the change feed through Realtime Broadcast instead of Postgres Changes

Revision ID: 26c67b32452c
Revises: c728ae43d901
Create Date: 2026-11-20 15:00:00.000000

Postgres Changes made Supabase Realtime poll the database about twice a second
while any device was signed in, and every poll cost ~12 ms of database CPU
whether or not anything had changed (19% of all database time by 2026-11-20).
Broadcast streams each announcement once, after the seller's save commits.

Transition: iPhone builds from before this change still follow seller_changes
through Postgres Changes, so the table stays in the supabase_realtime
publication. Remove it from the publication (a new revision: ALTER PUBLICATION
supabase_realtime DROP TABLE seller_changes, and drop the seller_subscriber
policy) by 2026-12-20, once the installed iPhone app uses Broadcast.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '26c67b32452c'
down_revision = 'c728ae43d901'
branch_labels = None
depends_on = None

# The subscriber's own channel: Realtime sets realtime.topic to the channel
# being joined and request.jwt.claims to the device's session.
OWN_CHANNEL = ("extension = 'broadcast' AND topic = (SELECT realtime.topic()) AND (SELECT realtime.topic()) = "
               "'seller-changes:' || (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    if not bind.scalar(sa.text("SELECT 1 FROM pg_namespace WHERE nspname = 'realtime'")):
        _create_realtime_stand_in()
    # Runs as the migration owner so a seller's session can announce without
    # any grant on realtime.messages. realtime.send only warns on failure, so a
    # failed announcement never fails the seller's save.
    op.execute(r"""
        CREATE FUNCTION anteroom_private.announce_seller_change() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        BEGIN
            PERFORM realtime.send(jsonb_build_object('topic', NEW.topic), 'change',
                                  'seller-changes:' || NEW.user_id, true);
            RETURN NULL;
        END $$""")
    op.execute('REVOKE ALL ON FUNCTION anteroom_private.announce_seller_change() FROM PUBLIC')
    op.execute('CREATE TRIGGER announce_seller_change AFTER INSERT OR UPDATE ON seller_changes '
               'FOR EACH ROW EXECUTE FUNCTION anteroom_private.announce_seller_change()')
    # A signed-in device may join only its own seller's private channel, and
    # only to receive: there is no insert policy, so no client can send on it.
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = 'authenticated'")):
        op.execute('CREATE POLICY anteroom_seller_changes_receive ON realtime.messages FOR SELECT '
                   f'TO authenticated USING ({OWN_CHANNEL})')


def _create_realtime_stand_in() -> None:
    """Plain Postgres (the hosted sandbox and tests) gets the part of Supabase's
    realtime schema the feed uses, the way 7c4e1a9f2b38 creates the publication."""
    op.execute('CREATE SCHEMA realtime')
    op.execute("""
        CREATE TABLE realtime.messages (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            topic text NOT NULL,
            extension text NOT NULL,
            payload jsonb,
            event text,
            private boolean DEFAULT false,
            inserted_at timestamptz NOT NULL DEFAULT now())""")
    op.execute('ALTER TABLE realtime.messages ENABLE ROW LEVEL SECURITY')
    op.execute("""
        CREATE FUNCTION realtime.topic() RETURNS text LANGUAGE sql STABLE AS $$
            SELECT nullif(current_setting('realtime.topic', true), '')::text $$""")
    op.execute("""
        CREATE FUNCTION realtime.send(payload jsonb, event text, topic text, private boolean DEFAULT true)
        RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
            -- Like Supabase's: a failed send warns and never fails the caller.
            BEGIN
                EXECUTE format('SET LOCAL realtime.topic TO %L', topic);
                INSERT INTO realtime.messages (payload, event, topic, private, extension)
                VALUES (payload, event, topic, private, 'broadcast');
            EXCEPTION WHEN OTHERS THEN
                RAISE WARNING 'WarnSendingBroadcastMessage: %', SQLERRM;
            END;
        END $$""")
    if op.get_bind().scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = 'authenticated'")):
        op.execute('GRANT USAGE ON SCHEMA realtime TO authenticated')
        # Supabase grants these too, so row security alone keeps a client from sending.
        op.execute('GRANT SELECT, INSERT, UPDATE ON realtime.messages TO authenticated')


def downgrade() -> None:
    # The feed table and its publication stay (7c4e1a9f2b38), so devices on
    # Postgres Changes keep working. A stand-in realtime schema stays too.
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    op.execute('DROP POLICY IF EXISTS anteroom_seller_changes_receive ON realtime.messages')
    op.execute('DROP TRIGGER IF EXISTS announce_seller_change ON seller_changes')
    op.execute('DROP FUNCTION IF EXISTS anteroom_private.announce_seller_change()')
