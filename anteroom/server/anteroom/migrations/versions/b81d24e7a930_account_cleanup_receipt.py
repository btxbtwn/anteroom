"""Retain deletion cleanup targets until external cleanup succeeds.

Revision ID: b81d24e7a930
Revises: a70c21f496e8
"""
from alembic import op

revision = 'b81d24e7a930'
down_revision = 'a70c21f496e8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute('''
        CREATE TABLE anteroom_private.account_cleanup (
            user_id uuid PRIMARY KEY,
            object_keys text[] NOT NULL
        );
        REVOKE ALL ON anteroom_private.account_cleanup FROM PUBLIC, anteroom_seller;
        CREATE FUNCTION anteroom_private.stage_account_deletion()
        RETURNS SETOF text LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE owner_id uuid := anteroom_private.seller_id()::uuid;
        BEGIN
            INSERT INTO anteroom_private.account_cleanup AS pending (user_id, object_keys)
            VALUES (owner_id, ARRAY(SELECT anteroom_private.account_photo_keys()))
            ON CONFLICT (user_id) DO UPDATE SET object_keys = ARRAY(
                SELECT DISTINCT unnest(pending.object_keys || EXCLUDED.object_keys));
            RETURN QUERY SELECT unnest(object_keys) FROM anteroom_private.account_cleanup
                WHERE user_id = owner_id;
        END $$;
        CREATE FUNCTION anteroom_private.finish_account_deletion()
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE owner_id uuid := anteroom_private.seller_id()::uuid;
        BEGIN
            PERFORM anteroom_private.delete_account();
            DELETE FROM anteroom_private.account_cleanup WHERE user_id = owner_id;
        END $$;
        REVOKE ALL ON FUNCTION anteroom_private.stage_account_deletion() FROM PUBLIC;
        REVOKE ALL ON FUNCTION anteroom_private.finish_account_deletion() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION anteroom_private.stage_account_deletion() TO anteroom_seller;
        GRANT EXECUTE ON FUNCTION anteroom_private.finish_account_deletion() TO anteroom_seller;
    ''')


def downgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    # Refuse a downgrade that would discard unfinished cleanup work.
    op.execute('''DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM anteroom_private.account_cleanup) THEN
            RAISE EXCEPTION 'Finish pending account deletion cleanup before downgrading';
        END IF;
    END $$''')
    op.execute('DROP FUNCTION anteroom_private.finish_account_deletion()')
    op.execute('DROP FUNCTION anteroom_private.stage_account_deletion()')
    op.execute('DROP TABLE anteroom_private.account_cleanup')
