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
            PERFORM pg_advisory_xact_lock(hashtextextended('anteroom-account-delete:' || owner_id, 0));
            IF EXISTS (SELECT 1 FROM anteroom_private.account_cleanup WHERE user_id::text = owner_id) THEN
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
        op.execute('CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON storage.objects '
                   'FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()')
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
    raise RuntimeError('Deletion fences are forward-only; do not re-enable writes for deleted accounts')
