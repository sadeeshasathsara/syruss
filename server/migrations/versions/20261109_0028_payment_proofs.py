"""proof of payment: slips customers send for an invoice, which staff approve or reject

A customer who paid by bank transfer sends the slip (one or more files) with the amount, the day and the bank reference;
the payment counts once staff approve it. Files are kept privately and never deleted.

Revision ID: 0028
Revises: 0027
Created: 2026-11-09 15:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0028'
down_revision: Union[str, None] = '0027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'payment_submissions',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('invoice_id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('method', sa.String(length=32), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('paid_on', sa.Date(), nullable=False),
        sa.Column('payer_reference', sa.String(length=200), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('submitted_by', sa.String(length=100), nullable=False),
        sa.Column('submitted_by_name', sa.String(length=255), nullable=False),
        sa.Column('reviewed_by', sa.String(length=100), nullable=True),
        sa.Column('reviewed_by_name', sa.String(length=255), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('payment_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id']),
        sa.ForeignKeyConstraint(['payment_id'], ['billing_payments.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_payment_submissions_invoice_id', 'payment_submissions', ['invoice_id'])
    op.create_index('ix_payment_submissions_status_created', 'payment_submissions', ['status', 'created_at'])
    op.create_table(
        'payment_files',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('submission_id', sa.String(length=64), nullable=True),
        sa.Column('payment_id', sa.String(length=64), nullable=True),
        sa.Column('object_key', sa.String(length=300), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('uploaded_by', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['submission_id'], ['payment_submissions.id']),
        sa.ForeignKeyConstraint(['payment_id'], ['billing_payments.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_payment_files_submission_id', 'payment_files', ['submission_id'])
    op.create_index('ix_payment_files_payment_id', 'payment_files', ['payment_id'])
    op.create_index('ix_payment_files_sha256', 'payment_files', ['sha256'])
    # Kept as financial records: a file is never changed or removed.
    op.execute("""
        CREATE FUNCTION payment_files_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.object_key = '' THEN RETURN NEW; END IF;  -- the storage key is set once, right after insert
            RAISE EXCEPTION 'payment_files is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER payment_files_append_only BEFORE UPDATE OR DELETE ON payment_files
        FOR EACH ROW EXECUTE FUNCTION payment_files_append_only()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS payment_files_append_only ON payment_files")
    op.execute("DROP FUNCTION IF EXISTS payment_files_append_only()")
    op.drop_index('ix_payment_files_sha256', table_name='payment_files')
    op.drop_index('ix_payment_files_payment_id', table_name='payment_files')
    op.drop_index('ix_payment_files_submission_id', table_name='payment_files')
    op.drop_table('payment_files')
    op.drop_index('ix_payment_submissions_status_created', table_name='payment_submissions')
    op.drop_index('ix_payment_submissions_invoice_id', table_name='payment_submissions')
    op.drop_table('payment_submissions')
