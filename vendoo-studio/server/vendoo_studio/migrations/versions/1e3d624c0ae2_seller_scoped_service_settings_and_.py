"""Seller-scoped service settings and Vault references

Revision ID: 1e3d624c0ae2
Revises: c93392ae8c16
Create Date: 2026-10-30 05:25:26.795809
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '1e3d624c0ae2'
down_revision = 'c93392ae8c16'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if 'user_secret_references' not in sa.inspect(bind).get_table_names():
        op.create_table('user_secret_references',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('account', sa.String(100), nullable=False),
            sa.Column('secret_id', sa.Uuid(as_uuid=False), nullable=False),
            sa.Column('user_id', sa.String(36), nullable=False,
                      server_default='00000000-0000-0000-0000-000000000000'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'account', name='uq_user_secret_account'))
        op.create_index('ix_user_secret_references_user_id', 'user_secret_references', ['user_id'])
    if bind.dialect.name == 'postgresql':
        owner = "user_id = (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') AND user_id <> '00000000-0000-0000-0000-000000000000'"
        op.execute('ALTER TABLE user_secret_references ENABLE ROW LEVEL SECURITY')
        op.execute('CREATE POLICY seller_owner ON user_secret_references AS RESTRICTIVE FOR ALL TO PUBLIC '
                   f'USING ({owner}) WITH CHECK ({owner})')
        op.execute('CREATE POLICY seller_access ON user_secret_references FOR SELECT TO anteroom_seller USING (true)')
        # The runtime can inspect only its own references. Only the private
        # owner-checked functions may change a pointer or access Vault.
        op.execute('REVOKE ALL ON user_secret_references FROM PUBLIC, anteroom_seller')
        op.execute('GRANT SELECT ON user_secret_references TO anteroom_seller')
        for role in ('anon', 'authenticated'):
            if bind.scalar(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}):
                op.execute(f'REVOKE ALL ON user_secret_references FROM {role}')
        op.execute('CREATE SCHEMA IF NOT EXISTS anteroom_private')
        op.execute('REVOKE ALL ON SCHEMA anteroom_private FROM PUBLIC')
        op.execute('GRANT USAGE ON SCHEMA anteroom_private TO anteroom_seller')
        op.execute("""
            CREATE OR REPLACE FUNCTION anteroom_private.seller_id()
            RETURNS text LANGUAGE plpgsql STABLE SET search_path = '' AS $$
            DECLARE owner_id text := nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub';
            BEGIN
                IF owner_id IS NULL OR owner_id = '00000000-0000-0000-0000-000000000000'
                   OR owner_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
                    RAISE insufficient_privilege USING MESSAGE = 'A verified seller is required';
                END IF;
                RETURN owner_id;
            END $$
        """)
        op.execute("""
            CREATE OR REPLACE FUNCTION anteroom_private.secret_read(slot text)
            RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
            DECLARE owner_id text := anteroom_private.seller_id(); value text;
            BEGIN
                SELECT secret.decrypted_secret INTO value
                FROM public.user_secret_references AS reference
                JOIN vault.decrypted_secrets AS secret ON secret.id = reference.secret_id
                WHERE reference.user_id = owner_id AND reference.account = slot
                  AND secret.name = 'anteroom:' || owner_id || ':' || slot;
                RETURN value;
            END $$
        """)
        op.execute("""
            CREATE OR REPLACE FUNCTION anteroom_private.secret_write(slot text, value text)
            RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
            DECLARE owner_id text := anteroom_private.seller_id(); known_id uuid;
                    secret_name text := 'anteroom:' || owner_id || ':' || slot;
            BEGIN
                IF slot IS NULL OR length(slot) < 1 OR length(slot) > 100 OR length(value) > 131072 THEN
                    RAISE invalid_parameter_value USING MESSAGE = 'Invalid credential slot';
                END IF;
                PERFORM pg_advisory_xact_lock(hashtextextended('anteroom-secret:' || owner_id || ':' || slot, 0));
                SELECT secret_id INTO known_id FROM public.user_secret_references
                    WHERE user_id = owner_id AND account = slot FOR UPDATE;
                IF value IS NULL THEN
                    DELETE FROM vault.secrets WHERE id = known_id AND name = secret_name;
                    DELETE FROM public.user_secret_references WHERE user_id = owner_id AND account = slot;
                ELSE
                    IF known_id IS NULL THEN
                        known_id := vault.create_secret(value, secret_name, 'Anteroom account credential');
                    ELSE
                        IF NOT EXISTS (SELECT 1 FROM vault.secrets WHERE id = known_id AND name = secret_name) THEN
                            RAISE insufficient_privilege USING MESSAGE = 'Credential ownership mismatch';
                        END IF;
                        PERFORM vault.update_secret(known_id, value, secret_name, 'Anteroom account credential');
                    END IF;
                    INSERT INTO public.user_secret_references (id, user_id, account, secret_id)
                        VALUES (replace(gen_random_uuid()::text, '-', ''), owner_id, slot, known_id)
                        ON CONFLICT (user_id, account) DO UPDATE SET secret_id = excluded.secret_id;
                END IF;
            END $$
        """)
        for signature in ('seller_id()', 'secret_read(text)', 'secret_write(text,text)'):
            op.execute(f'REVOKE ALL ON FUNCTION anteroom_private.{signature} FROM PUBLIC')
        for signature in ('secret_read(text)', 'secret_write(text,text)'):
            op.execute(f'GRANT EXECUTE ON FUNCTION anteroom_private.{signature} TO anteroom_seller')
    _move_settings(bind)


def _move_settings(bind) -> None:
    """Put existing cloud preferences behind the shared settings service."""
    import uuid
    table = sa.Table('user_preferences', sa.MetaData(), autoload_with=bind)
    groups = {}
    for row in bind.execute(sa.select(table)).mappings():
        groups.setdefault(row['user_id'], {})[row['key']] = row
    for owner, records in groups.items():
        current = records.get('settings')
        payload = dict(current['value']) if current and isinstance(current['value'], dict) else {}
        moved = []
        for old, new in (('ui', 'ui'), ('package-dimensions', 'package_dimensions_in')):
            row = records.get(old)
            if row:
                payload.setdefault(new, row['value'])
                moved.append(old)
        location = records.get('inventory-location')
        if location:
            payload.setdefault('ebay_inventory_location', location['value'].get('location', ''))
            moved.append('inventory-location')
        hidden = records.get('hidden-fields')
        per_listing = {key.removeprefix('hidden:'): row['value'].get('fields', [])
                       for key, row in records.items() if key.startswith('hidden:')}
        if hidden or per_listing:
            payload.setdefault('hidden_fields', {'always': hidden['value'].get('fields', []) if hidden else [],
                                                 'listings': per_listing})
            moved.extend(key for key in records if key == 'hidden-fields' or key.startswith('hidden:'))
        defaults = records.get('listing-defaults')
        if defaults:
            values = defaults['value']
            zip_value = values.get('mercari_specifics.shipsFrom', {}).get('value')
            if zip_value:
                payload.setdefault('ships_from_zip', zip_value)
            label = values.get('mercari_specifics.shippingClassId')
            if label:
                payload.setdefault('mercari_shipping_label', {'id': label.get('value'), 'label': label.get('label')})
            moved.append('listing-defaults')
        if not moved:
            continue
        if current:
            bind.execute(table.update().where(table.c.id == current['id']).values(value=payload))
        else:
            bind.execute(table.insert().values(id=uuid.uuid4().hex, user_id=owner, key='settings', value=payload))
        bind.execute(table.delete().where(table.c.user_id == owner, table.c.key.in_(moved)))


def downgrade() -> None:
    raise RuntimeError('Never remove stored account credentials or preferences')
