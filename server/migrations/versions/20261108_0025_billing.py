"""plans belong to the organization: a versioned catalog, subscriptions, add-ons, overrides and pages used

Plans used to be set per workspace from a list in the code. Now the catalog (plans and add-ons, one row per version)
lives in the database, each organization has one subscription, and its workspaces share the plan's monthly page
allowance. Every document accepted for reading writes a usage event for its pages; those only grow.

Existing organizations keep what they had: each is put on the highest plan any of its workspaces was on, and where its
workspaces together had more documents a month than that plan gives, an override carries the difference over. This
month's documents are counted as used pages.

Revision ID: 0025
Revises: 0024
Created: 2026-11-08 09:00:00
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0025'
down_revision: Union[str, None] = '0024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Version 1 of the plans that were in the code (app.services.billing.catalog.DEFAULT_PLANS holds the same).
PLANS = [
    ("plan-free-1", "free", "Free", 0, 0, {"pages_per_month": 200, "workspaces": None, "members_per_workspace": 3, "api_keys_per_workspace": 1,
                                         "webhooks_per_workspace": 1, "channels_per_workspace": 1, "destinations_per_workspace": 1}),
    ("plan-team-1", "team", "Team", 4900, 1, {"pages_per_month": 2000, "workspaces": None, "members_per_workspace": 15, "api_keys_per_workspace": 10,
                                            "webhooks_per_workspace": 5, "channels_per_workspace": 5, "destinations_per_workspace": 5}),
    ("plan-business-1", "business", "Business", 19900, 2, {"pages_per_month": 20000, "workspaces": None, "members_per_workspace": 100,
                                                         "api_keys_per_workspace": 50, "webhooks_per_workspace": 20, "channels_per_workspace": 25,
                                                         "destinations_per_workspace": 25}),
]


def _add_month(moment: datetime) -> datetime:
    return moment.replace(year=moment.year + 1, month=1) if moment.month == 12 else moment.replace(month=moment.month + 1)


def upgrade() -> None:
    op.add_column('documents', sa.Column('page_count', sa.Integer(), server_default='1', nullable=False))

    op.create_table(
        'billing_catalog',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('price_cents', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('interval', sa.String(length=16), nullable=False),
        sa.Column('public', sa.Boolean(), nullable=False),
        sa.Column('sort', sa.Integer(), nullable=False),
        sa.Column('limits', sa.JSON(), nullable=False),
        sa.Column('provider_ref', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('kind', 'key', 'version', name='uq_billing_catalog_version'),
    )
    op.create_table(
        'org_subscriptions',
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('plan_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('anchor_day', sa.Integer(), nullable=False),
        sa.Column('trial_ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('pending_plan_id', sa.String(length=64), nullable=True),
        sa.Column('paid_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('provider_ref', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['plan_id'], ['billing_catalog.id']),
        sa.ForeignKeyConstraint(['pending_plan_id'], ['billing_catalog.id']),
        sa.PrimaryKeyConstraint('tenant_id'),
    )
    op.create_table(
        'org_subscription_addons',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('addon_id', sa.String(length=64), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('added_by', sa.String(length=100), nullable=False),
        sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['addon_id'], ['billing_catalog.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'addon_id', name='uq_org_subscription_addon'),
    )
    op.create_index('ix_org_subscription_addons_tenant_id', 'org_subscription_addons', ['tenant_id'])
    op.create_table(
        'org_limit_overrides',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('template_id', sa.String(length=64), nullable=True),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('value', sa.Integer(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('set_by', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['template_id'], ['document_templates.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_org_limit_overrides_tenant_key', 'org_limit_overrides', ['tenant_id', 'key'])
    op.create_table(
        'usage_events',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('template_id', sa.String(length=64), nullable=True),
        sa.Column('metric', sa.String(length=32), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('from_credits', sa.Integer(), nullable=False),
        sa.Column('source_type', sa.String(length=32), nullable=False),
        sa.Column('source_id', sa.String(length=64), nullable=False),
        sa.Column('idempotency_key', sa.String(length=160), nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key'),
    )
    op.create_index('ix_usage_events_tenant_period', 'usage_events', ['tenant_id', 'metric', 'period_start'])
    op.execute("""
        CREATE FUNCTION usage_events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'usage_events is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER usage_events_append_only BEFORE UPDATE OR DELETE ON usage_events
        FOR EACH ROW EXECUTE FUNCTION usage_events_append_only()
    """)

    _carry_over()
    op.execute("UPDATE workspace_settings SET data = (data::jsonb - 'plan')::json")


def _carry_over() -> None:
    """Seeds the plans and moves every organization onto one, keeping the capacity its workspaces had."""
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    catalog = sa.table('billing_catalog', *(sa.column(c) for c in (
        'id', 'kind', 'key', 'version', 'name', 'status', 'price_cents', 'currency', 'interval', 'public', 'sort', 'created_by',
        'created_at', 'published_at')), sa.column('limits', sa.JSON), sa.column('provider_ref', sa.JSON))
    op.bulk_insert(catalog, [{
        "id": pid, "kind": "plan", "key": key, "version": 1, "name": name, "status": "published", "price_cents": price, "currency": "USD",
        "interval": "month", "public": True, "sort": sort, "limits": limits, "provider_ref": {}, "created_by": "migration",
        "created_at": now, "published_at": now,
    } for pid, key, name, price, sort, limits in PLANS])

    rank = {key: i for i, (_, key, *_rest) in enumerate(PLANS)}
    by_key = {key: (pid, limits["pages_per_month"]) for pid, key, _, _, _, limits in PLANS}
    plans_of = {}
    for tenant_id, data in conn.execute(sa.text(
            "SELECT t.id, s.data FROM tenants t LEFT JOIN document_templates d ON d.tenant_id = t.id "
            "LEFT JOIN workspace_settings s ON s.template_id = d.id")).all():
        stored = data if isinstance(data, dict) else json.loads(data) if data else {}
        plans_of.setdefault(tenant_id, []).append(stored.get("plan") if stored.get("plan") in rank else "free")

    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)  # usage was counted by calendar month until now
    end = _add_month(start)
    for tenant_id, keys in plans_of.items():
        best = max(keys, key=rank.__getitem__)
        conn.execute(sa.text(
            "INSERT INTO org_subscriptions (tenant_id, plan_id, status, period_start, period_end, anchor_day, provider_ref, created_at, updated_at) "
            "VALUES (:t, :p, 'active', :s, :e, 1, '{}', :now, :now)"), {"t": tenant_id, "p": by_key[best][0], "s": start, "e": end, "now": now})
        had = sum(by_key[k][1] for k in keys)
        if had > by_key[best][1]:
            conn.execute(sa.text(
                "INSERT INTO org_limit_overrides (id, tenant_id, key, value, reason, set_by, created_at) "
                "VALUES (:id, :t, 'pages_per_month', :v, 'Carried over from per-workspace plans', 'migration', :now)"),
                {"id": str(uuid.uuid4()), "t": tenant_id, "v": had, "now": now})

    conn.execute(sa.text(
        "INSERT INTO usage_events (id, tenant_id, template_id, metric, quantity, from_credits, source_type, source_id, idempotency_key, "
        "period_start, occurred_at) "
        "SELECT gen_random_uuid()::text, tenant_id, template_id, 'pages', 1, 0, 'document', id, 'pages:' || id, :s, created_at "
        "FROM documents WHERE role = 'DOCUMENT' AND NOT is_test AND created_at >= :s"), {"s": start})


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS usage_events_append_only ON usage_events")
    op.execute("DROP FUNCTION IF EXISTS usage_events_append_only()")
    op.drop_index('ix_usage_events_tenant_period', table_name='usage_events')
    op.drop_table('usage_events')
    op.drop_index('ix_org_limit_overrides_tenant_key', table_name='org_limit_overrides')
    op.drop_table('org_limit_overrides')
    op.drop_index('ix_org_subscription_addons_tenant_id', table_name='org_subscription_addons')
    op.drop_table('org_subscription_addons')
    op.drop_table('org_subscriptions')
    op.drop_table('billing_catalog')
    op.drop_column('documents', 'page_count')
