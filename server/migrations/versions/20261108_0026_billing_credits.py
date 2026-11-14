"""prepaid credits, payments recorded by staff, and customers' billing requests

Beyond its plan's monthly pages, an organization reads on prepaid credits: packs of pages in the catalog, kept in a
ledger that only grows. Until a payment provider is connected, staff record the payments they receive, and customers
ask for a plan, an add-on or credits with a billing request. Usage alerts are sent once a period each.

Revision ID: 0026
Revises: 0025
Created: 2026-11-08 12:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0026'
down_revision: Union[str, None] = '0025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('billing_catalog', sa.Column('pages', sa.Integer(), nullable=True))
    op.add_column('org_subscriptions', sa.Column('alerts', sa.JSON(), server_default='{}', nullable=False))
    op.alter_column('org_subscriptions', 'alerts', server_default=None)

    op.create_table(
        'credit_ledger',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('delta', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('ref', sa.String(length=160), nullable=True),
        sa.Column('created_by', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_credit_ledger_tenant_created', 'credit_ledger', ['tenant_id', 'created_at'])
    op.execute("""
        CREATE FUNCTION credit_ledger_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'credit_ledger is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER credit_ledger_append_only BEFORE UPDATE OR DELETE ON credit_ledger
        FOR EACH ROW EXECUTE FUNCTION credit_ledger_append_only()
    """)

    op.create_table(
        'billing_payments',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('item_id', sa.String(length=64), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('reference', sa.String(length=200), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('recorded_by', sa.String(length=100), nullable=False),
        sa.Column('recorded_by_name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['billing_catalog.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_billing_payments_tenant_created', 'billing_payments', ['tenant_id', 'created_at'])

    op.create_table(
        'billing_requests',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('item_id', sa.String(length=64), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('requested_by', sa.String(length=100), nullable=False),
        sa.Column('requested_by_name', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('handled_by_name', sa.String(length=255), nullable=True),
        sa.Column('handled_note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['item_id'], ['billing_catalog.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_billing_requests_tenant_id', 'billing_requests', ['tenant_id'])
    op.create_index('ix_billing_requests_status_created', 'billing_requests', ['status', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_billing_requests_status_created', table_name='billing_requests')
    op.drop_index('ix_billing_requests_tenant_id', table_name='billing_requests')
    op.drop_table('billing_requests')
    op.drop_index('ix_billing_payments_tenant_created', table_name='billing_payments')
    op.drop_table('billing_payments')
    op.execute("DROP TRIGGER IF EXISTS credit_ledger_append_only ON credit_ledger")
    op.execute("DROP FUNCTION IF EXISTS credit_ledger_append_only()")
    op.drop_index('ix_credit_ledger_tenant_created', table_name='credit_ledger')
    op.drop_table('credit_ledger')
    op.drop_column('org_subscriptions', 'alerts')
    op.drop_column('billing_catalog', 'pages')
