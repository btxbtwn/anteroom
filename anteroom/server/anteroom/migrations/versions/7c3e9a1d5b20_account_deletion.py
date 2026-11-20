"""Let a signed-in seller delete their own account

Revision ID: 7c3e9a1d5b20
Revises: e3bea75f1b9b
Create Date: 2026-11-06 12:00:00.000000

Three private functions, callable only by the restricted seller role and only
for the seller named in the transaction's verified claim
(``anteroom_private.seller_id()``). The hosted API holds no service-role key:
these functions are the narrow privilege that deleting an account needs.

- ``account_deletion_ready()``: whether this database lets the functions finish
  (on Supabase, the migration owner may delete from ``auth.users`` and Vault).
- ``account_photo_keys()``: the seller's object names in the private photo
  bucket, so the API can remove them through the Storage API with the seller's
  own session. Supabase refuses direct deletes from ``storage.objects``.
- ``delete_account()``: removes the seller's Vault secrets, their secret
  references and device routes, then their Supabase Auth user (which ends
  every session and refresh token). Seller table rows are deleted beforehand
  by the API through the row-secured seller session.

Ordinary Postgres (CI, sandboxes) has no ``auth``, ``vault`` or ``storage``
schema; each step checks for its table first.
"""
from __future__ import annotations

from alembic import op


revision = '7c3e9a1d5b20'
down_revision = 'e3bea75f1b9b'
branch_labels = None
depends_on = None

FUNCTIONS = ('account_deletion_ready()', 'account_photo_keys()', 'delete_account()')


def upgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute("""
        CREATE FUNCTION anteroom_private.account_deletion_ready()
        RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
        BEGIN
            PERFORM anteroom_private.seller_id();
            IF to_regclass('auth.users') IS NOT NULL AND NOT has_table_privilege('auth.users', 'DELETE') THEN
                RETURN false;
            END IF;
            IF to_regclass('vault.secrets') IS NOT NULL AND NOT has_table_privilege('vault.secrets', 'DELETE') THEN
                RETURN false;
            END IF;
            IF to_regclass('storage.objects') IS NOT NULL AND NOT has_table_privilege('storage.objects', 'SELECT') THEN
                RETURN false;
            END IF;
            RETURN true;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION anteroom_private.account_photo_keys()
        RETURNS SETOF text LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
        DECLARE owner_id text := anteroom_private.seller_id();
        BEGIN
            IF to_regclass('storage.objects') IS NULL THEN
                RETURN;
            END IF;
            -- owner_id is a checked UUID, so it holds no LIKE wildcards.
            RETURN QUERY SELECT object.name FROM storage.objects AS object
                WHERE object.bucket_id = 'anteroom-photos' AND object.name LIKE owner_id || '/%'
                ORDER BY object.name;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION anteroom_private.delete_account()
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
        DECLARE owner_id text := anteroom_private.seller_id();
        BEGIN
            -- By name, as secret_write checks: a corrupt reference never reaches another seller's secret.
            IF to_regclass('vault.secrets') IS NOT NULL THEN
                DELETE FROM vault.secrets WHERE name LIKE 'anteroom:' || owner_id || ':%';
            END IF;
            DELETE FROM public.user_secret_references WHERE user_id = owner_id;
            IF to_regclass('anteroom_private.device_routes') IS NOT NULL THEN
                DELETE FROM anteroom_private.device_routes WHERE user_id = owner_id::uuid;
            END IF;
            IF to_regclass('auth.users') IS NOT NULL THEN
                DELETE FROM auth.users WHERE id = owner_id::uuid;
            END IF;
        END $$
    """)
    for signature in FUNCTIONS:
        op.execute(f'REVOKE ALL ON FUNCTION anteroom_private.{signature} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION anteroom_private.{signature} TO anteroom_seller')


def downgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    for signature in FUNCTIONS:
        op.execute(f'DROP FUNCTION anteroom_private.{signature}')
