"""Scheduled draft publishing, per-marketplace auto-relist, waterfall/bundle/follow promotions, and 60-day sale sync log.

Revision ID: c2d9f4a8e103
Revises: 049b76a1ce20
Create Date: 2026-11-22 10:45:00.000000
"""
from __future__ import annotations

from contextlib import nullcontext

from alembic import op
import sqlalchemy as sa


revision = 'c2d9f4a8e103'
down_revision = '049b76a1ce20'
branch_labels = None
depends_on = None

OWNER = ("user_id = (SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub') "
         "AND user_id <> '00000000-0000-0000-0000-000000000000'")

NEW_TABLES = (
    'scheduled_publish_logs',
    'marketplace_relist_rules',
    'marketplace_relist_logs',
    'sale_sync_events',
)


def _add_column_if_missing(inspector, table_name: str, column: sa.Column) -> None:
    if not inspector.has_table(table_name):
        return
    existing = {col['name'] for col in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # 1. Draft scheduling columns on listings (all OFF / null by default).
    _add_column_if_missing(inspector, 'listings', sa.Column('ready_to_list', sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_column_if_missing(inspector, 'listings', sa.Column('scheduled_publish_at', sa.DateTime(), nullable=True))
    _add_column_if_missing(inspector, 'listings', sa.Column('scheduled_marketplaces', sa.JSON(), nullable=True))
    _add_column_if_missing(inspector, 'listings', sa.Column('scheduled_revision_id', sa.String(), nullable=True))
    _add_column_if_missing(inspector, 'listings', sa.Column('scheduled_confirmed_at', sa.DateTime(), nullable=True))
    _add_column_if_missing(inspector, 'listings', sa.Column('scheduled_status', sa.String(32), nullable=True))
    _add_column_if_missing(inspector, 'listings', sa.Column('scheduled_error', sa.Text(), nullable=True))
    if 'listings' in tables:
        postgres = bind.dialect.name == 'postgresql'
        name = 'ix_listings_scheduled_publish_at'
        with op.get_context().autocommit_block() if postgres else nullcontext():
            if postgres:
                valid = op.get_bind().scalar(sa.text(
                    'SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)'
                ), {'name': name})
                if valid is False:
                    op.drop_index(name, table_name='listings', postgresql_concurrently=True)
                exists = valid is True
            else:
                exists = name in {index['name'] for index in inspector.get_indexes('listings')}
            if not exists:
                op.create_index(name, 'listings', ['scheduled_publish_at'], postgresql_concurrently=True)

    # 2. Promotion automation parity columns (waterfall, bundles, party/community shares, 24h schedule, follows).
    _add_column_if_missing(inspector, 'promotion_automations', sa.Column('share_modes', sa.JSON(), nullable=True))
    _add_column_if_missing(inspector, 'promotion_automations', sa.Column('hourly_schedule', sa.JSON(), nullable=True))
    _add_column_if_missing(inspector, 'promotion_automations', sa.Column('waterfall_steps', sa.JSON(), nullable=True))
    _add_column_if_missing(inspector, 'promotion_automations', sa.Column('bundle_pause_minutes', sa.Integer(), nullable=True))
    _add_column_if_missing(inspector, 'promotion_automations', sa.Column('bundle_comment', sa.Text(), nullable=True))
    _add_column_if_missing(inspector, 'promotion_automations', sa.Column('bundle_min_items', sa.Integer(), nullable=True))
    _add_column_if_missing(inspector, 'promotion_automations', sa.Column('follow_config', sa.JSON(), nullable=True))

    _add_column_if_missing(inspector, 'listing_promotions', sa.Column('follow_budget', sa.JSON(), nullable=True))
    _add_column_if_missing(inspector, 'listing_promotions', sa.Column('waterfall_step', sa.Integer(), nullable=True))
    _add_column_if_missing(inspector, 'listing_promotions', sa.Column('buyer_handle', sa.String(), nullable=True))

    _add_column_if_missing(inspector, 'ebay_offer_policies', sa.Column('waterfall_steps', sa.JSON(), nullable=True))
    _add_column_if_missing(inspector, 'ebay_offer_rules', sa.Column('waterfall_steps', sa.JSON(), nullable=True))
    _add_column_if_missing(inspector, 'ebay_offer_attempts', sa.Column('waterfall_step', sa.Integer(), nullable=True))

    # 3. New seller-owned tables.
    if 'scheduled_publish_logs' not in tables:
        op.create_table(
            'scheduled_publish_logs',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('user_id', sa.String(36), nullable=False, server_default='00000000-0000-0000-0000-000000000000'),
            sa.Column('conversation_id', sa.String(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('revision_id', sa.String(), nullable=True),
            sa.Column('marketplaces', sa.JSON(), nullable=False),
            sa.Column('scheduled_for', sa.DateTime(), nullable=False),
            sa.Column('status', sa.String(32), nullable=False),
            sa.Column('results', sa.JSON(), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('finished_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(
                ['user_id', 'conversation_id'],
                ['conversations.user_id', 'conversations.id'],
                name='fk_scheduled_publish_logs_seller_conversation_id',
                deferrable=True,
                initially='DEFERRED',
            ),
        )
        op.create_index('ix_scheduled_publish_logs_user_id', 'scheduled_publish_logs', ['user_id'])
        op.create_index('ix_scheduled_publish_logs_conversation_id', 'scheduled_publish_logs', ['conversation_id'])
        op.create_index('ix_scheduled_publish_logs_owner_created', 'scheduled_publish_logs', ['user_id', 'created_at'])

    if 'marketplace_relist_rules' not in tables:
        op.create_table(
            'marketplace_relist_rules',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('user_id', sa.String(36), nullable=False, server_default='00000000-0000-0000-0000-000000000000'),
            sa.Column('marketplace', sa.String(32), nullable=False),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('min_age_days', sa.Integer(), nullable=False, server_default='30'),
            sa.Column('ebay_recreate_before_gtc_minutes', sa.Integer(), nullable=True),
            sa.Column('daily_cap', sa.Integer(), nullable=False, server_default='25'),
            sa.Column('price_adjust_mode', sa.String(16), nullable=True),
            sa.Column('price_adjust_value', sa.Float(), nullable=True),
            sa.Column('price_floor', sa.Float(), nullable=True),
            sa.Column('filters', sa.JSON(), nullable=False),
            sa.Column('skip_active_offers', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('last_run_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'marketplace', name='uq_marketplace_relist_rules_seller_market'),
        )
        op.create_index('ix_marketplace_relist_rules_user_id', 'marketplace_relist_rules', ['user_id'])

    if 'marketplace_relist_logs' not in tables:
        op.create_table(
            'marketplace_relist_logs',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('user_id', sa.String(36), nullable=False, server_default='00000000-0000-0000-0000-000000000000'),
            sa.Column('conversation_id', sa.String(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('marketplace', sa.String(32), nullable=False),
            sa.Column('old_listing_id', sa.String(), nullable=True),
            sa.Column('new_listing_id', sa.String(), nullable=True),
            sa.Column('old_price', sa.Float(), nullable=True),
            sa.Column('new_price', sa.Float(), nullable=True),
            sa.Column('status', sa.String(32), nullable=False),
            sa.Column('reason', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(
                ['user_id', 'conversation_id'],
                ['conversations.user_id', 'conversations.id'],
                name='fk_marketplace_relist_logs_seller_conversation_id',
                deferrable=True,
                initially='DEFERRED',
            ),
        )
        op.create_index('ix_marketplace_relist_logs_user_id', 'marketplace_relist_logs', ['user_id'])
        op.create_index('ix_marketplace_relist_logs_conversation_id', 'marketplace_relist_logs', ['conversation_id'])
        op.create_index('ix_marketplace_relist_logs_owner_market_created', 'marketplace_relist_logs', ['user_id', 'marketplace', 'created_at'])

    if 'sale_sync_events' not in tables:
        op.create_table(
            'sale_sync_events',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('user_id', sa.String(36), nullable=False, server_default='00000000-0000-0000-0000-000000000000'),
            sa.Column('sale_id', sa.String(), nullable=False),
            sa.Column('conversation_id', sa.String(), sa.ForeignKey('conversations.id', ondelete='SET NULL'), nullable=True),
            sa.Column('sku', sa.String(), nullable=True),
            sa.Column('sold_marketplace', sa.String(32), nullable=False),
            sa.Column('target_marketplace', sa.String(32), nullable=False),
            sa.Column('action', sa.String(32), nullable=False),
            sa.Column('target_listing_id', sa.String(), nullable=True),
            sa.Column('new_listing_id', sa.String(), nullable=True),
            sa.Column('previous_quantity', sa.Integer(), nullable=True),
            sa.Column('new_quantity', sa.Integer(), nullable=True),
            sa.Column('status', sa.String(32), nullable=False, server_default='pending'),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('next_retry_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(
                ['user_id', 'conversation_id'],
                ['conversations.user_id', 'conversations.id'],
                name='fk_sale_sync_events_seller_conversation_id',
                deferrable=True,
                initially='DEFERRED',
            ),
        )
        op.create_index('ix_sale_sync_events_user_id', 'sale_sync_events', ['user_id'])
        op.create_index('ix_sale_sync_events_sale_id', 'sale_sync_events', ['sale_id'])
        op.create_index('ix_sale_sync_events_conversation_id', 'sale_sync_events', ['conversation_id'])
        op.create_index('ix_sale_sync_events_owner_created', 'sale_sync_events', ['user_id', 'created_at'])
        op.create_index('ix_sale_sync_events_sale_target', 'sale_sync_events', ['user_id', 'sale_id', 'target_marketplace'])

    if bind.dialect.name != 'postgresql':
        return

    for table in NEW_TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public."{table}" FROM PUBLIC')
        for role in ('anon', 'authenticated'):
            if bind.scalar(sa.text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}):
                op.execute(f'REVOKE ALL ON public."{table}" FROM {role}')
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON public."{table}" TO anteroom_seller')
        op.execute(
            f'CREATE POLICY seller_owner ON public."{table}" AS RESTRICTIVE FOR ALL TO PUBLIC '
            f'USING ({OWNER}) WITH CHECK ({OWNER})'
        )
        op.execute(
            f'CREATE POLICY seller_access ON public."{table}" FOR ALL TO anteroom_seller '
            'USING (true) WITH CHECK (true)'
        )
        op.execute(f'DROP TRIGGER IF EXISTS account_write_guard ON public."{table}"')
        op.execute(
            f'CREATE TRIGGER account_write_guard BEFORE INSERT OR UPDATE ON public."{table}" '
            'FOR EACH ROW EXECUTE FUNCTION anteroom_private.guard_account_write()'
        )

    _set_due_owners(bind, """
        UNION
        SELECT listing.user_id::text FROM public.listings AS listing
            WHERE listing.ready_to_list AND listing.scheduled_status = 'scheduled'
                AND listing.scheduled_confirmed_at IS NOT NULL
                AND listing.scheduled_publish_at <= (now() AT TIME ZONE 'utc')
        UNION
        SELECT rule.user_id::text FROM public.marketplace_relist_rules AS rule WHERE rule.enabled
    """)


def _set_due_owners(bind, extra: str) -> None:
    op.execute(f"""
        CREATE OR REPLACE FUNCTION anteroom_private.due_automation_owners()
        RETURNS TABLE(owner_id text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
        SELECT automation.user_id::text FROM public.automations AS automation
            WHERE automation.status = 'active' AND automation.next_run_at <= (now() AT TIME ZONE 'utc')
        UNION
        SELECT run.user_id::text FROM public.automation_runs AS run
            WHERE run.status IN ('running', 'waiting')
        {extra}
        $$;
        REVOKE ALL ON FUNCTION anteroom_private.due_automation_owners() FROM PUBLIC, anteroom_seller;
    """)
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname='anteroom_api'")):
        op.execute('GRANT EXECUTE ON FUNCTION anteroom_private.due_automation_owners() TO anteroom_api')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('LOCK TABLE anteroom_private.account_cleanup IN SHARE MODE')
        op.execute('''DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM anteroom_private.account_cleanup) THEN
                RAISE EXCEPTION 'Deletion fences cannot be removed while deleted or pending accounts exist';
            END IF;
        END $$''')
        _set_due_owners(bind, '')
        for table in NEW_TABLES:
            op.execute(f'DROP TRIGGER IF EXISTS account_write_guard ON public."{table}"')

    for table in reversed(NEW_TABLES):
        op.drop_table(table)

    for table, cols in (
        ('ebay_offer_attempts', ('waterfall_step',)),
        ('ebay_offer_rules', ('waterfall_steps',)),
        ('ebay_offer_policies', ('waterfall_steps',)),
        ('listing_promotions', ('buyer_handle', 'waterfall_step', 'follow_budget')),
        ('promotion_automations', (
            'follow_config', 'bundle_min_items', 'bundle_comment', 'bundle_pause_minutes',
            'waterfall_steps', 'hourly_schedule', 'share_modes',
        )),
        ('listings', (
            'scheduled_error', 'scheduled_status', 'scheduled_confirmed_at',
            'scheduled_revision_id', 'scheduled_marketplaces', 'scheduled_publish_at', 'ready_to_list',
        )),
    ):
        with op.batch_alter_table(table) as batch_op:
            if table == 'listings':
                batch_op.drop_index('ix_listings_scheduled_publish_at')
            for col in cols:
                batch_op.drop_column(col)
