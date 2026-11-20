"""Serialize seller writes with account deletion, including Storage metadata."""
from alembic import op
import sqlalchemy as sa

revision = 'c92e35f8b041'
down_revision = 'b81d24e7a930'
branch_labels = None
depends_on = None


def upgrade() -> None:
    db = op.get_bind()
    if db.dialect.name != 'postgresql':
        return
    op.execute('''
        CREATE FUNCTION anteroom_private.account_write_allowed(p_owner text) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE owner_uuid uuid;
        BEGIN
            PERFORM pg_advisory_xact_lock_shared(hashtextextended('anteroom-account-delete:' || p_owner, 0));
            BEGIN
                owner_uuid := p_owner::uuid;
            EXCEPTION WHEN invalid_text_representation THEN
                RETURN true;
            END;
            RETURN NOT EXISTS (
                SELECT 1 FROM anteroom_private.account_cleanup WHERE user_id = owner_uuid
            );
        END $$;
        REVOKE ALL ON FUNCTION anteroom_private.account_write_allowed(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION anteroom_private.account_write_allowed(text) TO PUBLIC;

        CREATE FUNCTION anteroom_private.guard_account_write() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE owner_id text;
        BEGIN
            IF TG_TABLE_SCHEMA = 'storage' THEN
                IF NEW.bucket_id <> 'anteroom-photos' THEN RETURN NEW; END IF;
                owner_id := split_part(NEW.name, '/', 1);
            ELSE
                owner_id := NEW.user_id::text;
            END IF;
            IF NOT anteroom_private.account_write_allowed(owner_id) THEN
                RAISE EXCEPTION 'Account deletion is pending' USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION anteroom_private.guard_account_write() FROM PUBLIC;
    ''')
    tables = db.execute(sa.text("""SELECT table_name FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'user_id'""")).scalars().all()
    for table in tables:
        quoted = db.dialect.identifier_preparer.quote(table)
        op.execute(f'CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON public.{quoted} '
                   'FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()')
    if db.scalar(sa.text("SELECT to_regclass('storage.objects')")) is not None:
        # Supabase owns storage.objects under supabase_storage_admin: enforce the
        # deletion fence via RESTRICTIVE RLS policies (matching c25ff7e26196) and
        # attach the trigger only when the migration role holds TRIGGER privilege.
        for action in ('INSERT', 'UPDATE'):
            op.execute(
                f"CREATE POLICY anteroom_photo_delete_fence_{action.lower()} ON storage.objects "
                f"AS RESTRICTIVE FOR {action} TO PUBLIC "
                "WITH CHECK (bucket_id <> 'anteroom-photos' "
                "OR anteroom_private.account_write_allowed(split_part(name, '/', 1)))"
            )
        op.execute('''DO $$ BEGIN
            CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON storage.objects
                FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write();
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END $$''')
    # Retain only the owner fence after completion: a still-valid JWT must not
    # resurrect records. No credentials or photo names remain in the receipt.
    op.execute('''CREATE OR REPLACE FUNCTION anteroom_private.finish_account_deletion()
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE owner_id uuid := anteroom_private.seller_id()::uuid;
        BEGIN
            PERFORM anteroom_private.delete_account();
            UPDATE anteroom_private.account_cleanup SET object_keys = ARRAY[]::text[] WHERE user_id = owner_id;
        END $$''')


def downgrade() -> None:
    db = op.get_bind()
    if db.dialect.name != 'postgresql':
        return
    # Refuse a downgrade that would re-enable writes for pending or deleted accounts.
    op.execute('''DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM anteroom_private.account_cleanup) THEN
            RAISE EXCEPTION 'Deletion fences cannot be removed while deleted or pending accounts exist';
        END IF;
    END $$''')
    op.execute('''CREATE OR REPLACE FUNCTION anteroom_private.finish_account_deletion()
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE owner_id uuid := anteroom_private.seller_id()::uuid;
        BEGIN
            PERFORM anteroom_private.delete_account();
            DELETE FROM anteroom_private.account_cleanup WHERE user_id = owner_id;
        END $$''')
    if db.scalar(sa.text("SELECT to_regclass('storage.objects')")) is not None:
        op.execute('''DO $$ BEGIN
            DROP TRIGGER IF EXISTS account_write_guard ON storage.objects;
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END $$''')
        for action in ('insert', 'update'):
            op.execute(f'DROP POLICY IF EXISTS anteroom_photo_delete_fence_{action} ON storage.objects')
    tables = db.execute(sa.text("""SELECT table_name FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'user_id'""")).scalars().all()
    for table in tables:
        quoted = db.dialect.identifier_preparer.quote(table)
        op.execute(f'DROP TRIGGER IF EXISTS account_write_guard ON public.{quoted}')
    op.execute('DROP FUNCTION IF EXISTS anteroom_private.guard_account_write()')
    op.execute('DROP FUNCTION IF EXISTS anteroom_private.account_write_allowed(text)')
