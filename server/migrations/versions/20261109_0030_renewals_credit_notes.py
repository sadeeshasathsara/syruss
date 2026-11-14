"""renewals and corrections: one renewal invoice a period, reminders sent, credit notes and refunds

Revision ID: 0030
Revises: 0029
Created: 2026-11-09 21:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0030'
down_revision: Union[str, None] = '0029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('invoices', sa.Column('amount_credited_cents', sa.Integer(), server_default='0', nullable=False))
    op.add_column('invoices', sa.Column('amount_refunded_cents', sa.Integer(), server_default='0', nullable=False))
    op.add_column('invoices', sa.Column('reminders', sa.JSON(), server_default='[]', nullable=False))
    for column in ('amount_credited_cents', 'amount_refunded_cents', 'reminders'):
        op.alter_column('invoices', column, server_default=None)
    op.create_unique_constraint('uq_invoices_period', 'invoices', ['tenant_id', 'kind', 'period_start'])

    op.create_table(
        'credit_notes',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('number', sa.String(length=40), nullable=False),
        sa.Column('invoice_id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('pdf_key', sa.String(length=300), nullable=True),
        sa.Column('created_by', sa.String(length=100), nullable=False),
        sa.Column('created_by_name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('number'),
    )
    op.create_index('ix_credit_notes_invoice_id', 'credit_notes', ['invoice_id'])
    op.create_table(
        'billing_refunds',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('payment_id', sa.String(length=64), nullable=False),
        sa.Column('credit_note_id', sa.String(length=64), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('method', sa.String(length=32), nullable=False),
        sa.Column('reference', sa.String(length=200), nullable=True),
        sa.Column('external_ref', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.String(length=100), nullable=False),
        sa.Column('created_by_name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['payment_id'], ['billing_payments.id']),
        sa.ForeignKeyConstraint(['credit_note_id'], ['credit_notes.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_ref'),
    )
    op.create_index('ix_billing_refunds_payment_id', 'billing_refunds', ['payment_id'])
    # Kept like invoices: a credit note's PDF is filled in once; otherwise neither changes or goes.
    op.execute("""
        CREATE FUNCTION credit_notes_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.pdf_key IS NULL AND NEW.pdf_key IS NOT NULL AND NEW.number = OLD.number
               AND NEW.amount_cents = OLD.amount_cents AND NEW.reason = OLD.reason THEN RETURN NEW; END IF;
            RAISE EXCEPTION 'credit_notes is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER credit_notes_append_only BEFORE UPDATE OR DELETE ON credit_notes FOR EACH ROW EXECUTE FUNCTION credit_notes_append_only()")
    op.execute("""
        CREATE FUNCTION billing_refunds_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'billing_refunds is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER billing_refunds_append_only BEFORE UPDATE OR DELETE ON billing_refunds FOR EACH ROW EXECUTE FUNCTION billing_refunds_append_only()")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS billing_refunds_append_only ON billing_refunds")
    op.execute("DROP FUNCTION IF EXISTS billing_refunds_append_only()")
    op.execute("DROP TRIGGER IF EXISTS credit_notes_append_only ON credit_notes")
    op.execute("DROP FUNCTION IF EXISTS credit_notes_append_only()")
    op.drop_index('ix_billing_refunds_payment_id', table_name='billing_refunds')
    op.drop_table('billing_refunds')
    op.drop_index('ix_credit_notes_invoice_id', table_name='credit_notes')
    op.drop_table('credit_notes')
    op.drop_constraint('uq_invoices_period', 'invoices', type_='unique')
    for column in ('reminders', 'amount_refunded_cents', 'amount_credited_cents'):
        op.drop_column('invoices', column)
