"""invoices: billing profiles, Syruss as the seller, tax rates, numbered invoices, payments against them

Every purchase is now an invoice from Syruss (billing_settings) to the organization (billing_profiles). Issuing one
numbers it from a gapless sequence and copies in both parties, the bank accounts and the tax; from then on the database
refuses to change what it says. Payments are recorded against invoices, however they were made, and only grow.

Open billing requests become draft invoices for staff to issue; the requests table goes.

Revision ID: 0027
Revises: 0026
Created: 2026-11-09 09:00:00
"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0027'
down_revision: Union[str, None] = '0026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FIXED = "an issued invoice is fixed: void it or correct it with a credit note"


def upgrade() -> None:
    op.create_table(
        'billing_profiles',
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('legal_name', sa.String(length=200), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('address_line1', sa.String(length=200), nullable=False),
        sa.Column('address_line2', sa.String(length=200), nullable=True),
        sa.Column('city', sa.String(length=120), nullable=False),
        sa.Column('region', sa.String(length=120), nullable=True),
        sa.Column('postal_code', sa.String(length=32), nullable=True),
        sa.Column('country', sa.String(length=2), nullable=False),
        sa.Column('tax_id', sa.String(length=64), nullable=True),
        sa.Column('po_number', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=100), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('tenant_id'),
    )
    op.create_table(
        'billing_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('legal_name', sa.String(length=200), nullable=False),
        sa.Column('address', sa.Text(), nullable=False),
        sa.Column('country', sa.String(length=2), nullable=False),
        sa.Column('tax_id', sa.String(length=64), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('invoice_prefix', sa.String(length=16), nullable=False),
        sa.Column('payment_terms_days', sa.Integer(), nullable=False),
        sa.Column('renewal_lead_days', sa.Integer(), nullable=False),
        sa.Column('grace_days', sa.Integer(), nullable=False),
        sa.Column('reminder_days', sa.JSON(), nullable=False),
        sa.Column('footer', sa.Text(), nullable=True),
        sa.Column('updated_by', sa.String(length=100), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'billing_bank_accounts',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('bank', sa.String(length=120), nullable=False),
        sa.Column('account_name', sa.String(length=200), nullable=False),
        sa.Column('account_number', sa.String(length=64), nullable=False),
        sa.Column('branch', sa.String(length=120), nullable=True),
        sa.Column('swift', sa.String(length=16), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('instructions', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'tax_rates',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('country', sa.String(length=2), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('rate_bp', sa.Integer(), nullable=False),
        sa.Column('reverse_charge_with_tax_id', sa.Boolean(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'billing_sequences',
        sa.Column('series', sa.String(length=40), nullable=False),
        sa.Column('last', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('series'),
    )

    op.create_table(
        'invoices',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('number', sa.String(length=40), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('subtotal_cents', sa.Integer(), nullable=False),
        sa.Column('tax_cents', sa.Integer(), nullable=False),
        sa.Column('total_cents', sa.Integer(), nullable=False),
        sa.Column('amount_paid_cents', sa.Integer(), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('void_reason', sa.Text(), nullable=True),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('seller', sa.JSON(), nullable=False),
        sa.Column('buyer', sa.JSON(), nullable=False),
        sa.Column('bank', sa.JSON(), nullable=False),
        sa.Column('tax_lines', sa.JSON(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('pdf_key', sa.String(length=300), nullable=True),
        sa.Column('receipt_key', sa.String(length=300), nullable=True),
        sa.Column('fulfilled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('switched_on_early_by', sa.String(length=255), nullable=True),
        sa.Column('switched_on_early_reason', sa.Text(), nullable=True),
        sa.Column('created_by', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('number'),
    )
    op.create_index('ix_invoices_tenant_created', 'invoices', ['tenant_id', 'created_at'])
    op.create_index('ix_invoices_status_due', 'invoices', ['status', 'due_at'])
    op.create_table(
        'invoice_lines',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('invoice_id', sa.String(length=64), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.String(length=64), nullable=True),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('description', sa.String(length=300), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('unit_cents', sa.Integer(), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('tax_rate_bp', sa.Integer(), nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['item_id'], ['billing_catalog.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_invoice_lines_invoice_id', 'invoice_lines', ['invoice_id'])

    # What an issued invoice says never changes, and an issued one is never removed.
    op.execute(f"""
        CREATE FUNCTION invoices_fixed_once_issued() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'draft' THEN RAISE EXCEPTION '{FIXED}'; END IF;
                RETURN OLD;
            END IF;
            IF OLD.status <> 'draft' AND (
                NEW.number IS DISTINCT FROM OLD.number OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                OR NEW.kind IS DISTINCT FROM OLD.kind OR NEW.currency IS DISTINCT FROM OLD.currency
                OR NEW.subtotal_cents IS DISTINCT FROM OLD.subtotal_cents OR NEW.tax_cents IS DISTINCT FROM OLD.tax_cents
                OR NEW.total_cents IS DISTINCT FROM OLD.total_cents OR NEW.issued_at IS DISTINCT FROM OLD.issued_at
                OR NEW.due_at IS DISTINCT FROM OLD.due_at OR NEW.note IS DISTINCT FROM OLD.note
                OR NEW.period_start IS DISTINCT FROM OLD.period_start OR NEW.period_end IS DISTINCT FROM OLD.period_end
                OR NEW.seller::text IS DISTINCT FROM OLD.seller::text OR NEW.buyer::text IS DISTINCT FROM OLD.buyer::text
                OR NEW.bank::text IS DISTINCT FROM OLD.bank::text OR NEW.tax_lines::text IS DISTINCT FROM OLD.tax_lines::text
                OR NEW.status = 'draft'
            ) THEN
                RAISE EXCEPTION '{FIXED}';
            END IF;
            IF OLD.status = 'void' AND NEW.status <> 'void' THEN RAISE EXCEPTION 'a void invoice stays void'; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER invoices_fixed_once_issued BEFORE UPDATE OR DELETE ON invoices
        FOR EACH ROW EXECUTE FUNCTION invoices_fixed_once_issued()
    """)
    op.execute(f"""
        CREATE FUNCTION invoice_lines_fixed_once_issued() RETURNS trigger AS $$
        DECLARE state text;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                SELECT status INTO state FROM invoices WHERE id = NEW.invoice_id;
            ELSE
                SELECT status INTO state FROM invoices WHERE id = OLD.invoice_id;
            END IF;
            IF state IS NOT NULL AND state <> 'draft' THEN RAISE EXCEPTION '{FIXED}'; END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER invoice_lines_fixed_once_issued BEFORE INSERT OR UPDATE OR DELETE ON invoice_lines
        FOR EACH ROW EXECUTE FUNCTION invoice_lines_fixed_once_issued()
    """)

    # Payments: against an invoice from now on, by a method; the ones staff recorded before keep their item.
    op.add_column('billing_payments', sa.Column('invoice_id', sa.String(length=64), nullable=True))
    op.add_column('billing_payments', sa.Column('method', sa.String(length=32), server_default='staff_recorded', nullable=False))
    op.alter_column('billing_payments', 'method', server_default=None)
    op.add_column('billing_payments', sa.Column('received_on', sa.Date(), nullable=True))
    op.add_column('billing_payments', sa.Column('external_ref', sa.String(length=200), nullable=True))
    op.create_foreign_key('billing_payments_invoice_id_fkey', 'billing_payments', 'invoices', ['invoice_id'], ['id'])
    op.create_index('ix_billing_payments_invoice_id', 'billing_payments', ['invoice_id'])
    op.execute("""
        CREATE FUNCTION billing_payments_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'billing_payments is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER billing_payments_append_only BEFORE UPDATE OR DELETE ON billing_payments
        FOR EACH ROW EXECUTE FUNCTION billing_payments_append_only()
    """)

    # Open requests become drafts staff issue (billing details may still be missing); handled ones are in the activity.
    conn = op.get_bind()
    waiting = conn.execute(sa.text(
        "SELECT r.tenant_id, r.item_id, r.quantity, r.note, r.requested_by, r.created_at, c.kind, c.name, c.price_cents, c.currency "
        "FROM billing_requests r JOIN billing_catalog c ON c.id = r.item_id WHERE r.status = 'open' ORDER BY r.created_at")).all()
    now = datetime.now(timezone.utc)
    for row in waiting:
        invoice_id, amount = str(uuid.uuid4()), row.price_cents * row.quantity
        conn.execute(sa.text(
            "INSERT INTO invoices (id, tenant_id, kind, status, currency, subtotal_cents, tax_cents, total_cents, amount_paid_cents, "
            "seller, buyer, bank, tax_lines, note, created_by, created_at, updated_at) VALUES (:id, :tenant, 'purchase', 'draft', :currency, "
            ":amount, 0, :amount, 0, '{}', '{}', '[]', '[]', :note, :by, :created, :now)"),
            {"id": invoice_id, "tenant": row.tenant_id, "currency": row.currency, "amount": amount, "note": row.note, "by": row.requested_by,
             "created": row.created_at, "now": now})
        conn.execute(sa.text(
            "INSERT INTO invoice_lines (id, invoice_id, position, item_id, kind, description, quantity, unit_cents, amount_cents, tax_rate_bp) "
            "VALUES (:id, :invoice, 0, :item, :kind, :name, :quantity, :unit, :amount, 0)"),
            {"id": str(uuid.uuid4()), "invoice": invoice_id, "item": row.item_id, "kind": row.kind, "name": row.name, "quantity": row.quantity,
             "unit": row.price_cents, "amount": amount})
    op.drop_index('ix_billing_requests_status_created', table_name='billing_requests')
    op.drop_index('ix_billing_requests_tenant_id', table_name='billing_requests')
    op.drop_table('billing_requests')


def downgrade() -> None:
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
    op.execute("DROP TRIGGER IF EXISTS billing_payments_append_only ON billing_payments")
    op.execute("DROP FUNCTION IF EXISTS billing_payments_append_only()")
    op.drop_index('ix_billing_payments_invoice_id', table_name='billing_payments')
    op.drop_constraint('billing_payments_invoice_id_fkey', 'billing_payments', type_='foreignkey')
    for column in ('external_ref', 'received_on', 'method', 'invoice_id'):
        op.drop_column('billing_payments', column)
    op.execute("DROP TRIGGER IF EXISTS invoice_lines_fixed_once_issued ON invoice_lines")
    op.execute("DROP FUNCTION IF EXISTS invoice_lines_fixed_once_issued()")
    op.execute("DROP TRIGGER IF EXISTS invoices_fixed_once_issued ON invoices")
    op.execute("DROP FUNCTION IF EXISTS invoices_fixed_once_issued()")
    op.drop_index('ix_invoice_lines_invoice_id', table_name='invoice_lines')
    op.drop_table('invoice_lines')
    op.drop_index('ix_invoices_status_due', table_name='invoices')
    op.drop_index('ix_invoices_tenant_created', table_name='invoices')
    op.drop_table('invoices')
    op.drop_table('billing_sequences')
    op.drop_table('tax_rates')
    op.drop_table('billing_bank_accounts')
    op.drop_table('billing_settings')
    op.drop_table('billing_profiles')
