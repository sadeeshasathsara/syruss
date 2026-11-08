"""card payments: Stripe Checkout pages for invoices, the events Stripe sends, a card kept for renewals

Revision ID: 0029
Revises: 0028
Created: 2026-11-09 18:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0029'
down_revision: Union[str, None] = '0028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('billing_profiles', sa.Column('stripe_customer_id', sa.String(length=255), nullable=True))
    op.add_column('billing_profiles', sa.Column('card_payment_method', sa.String(length=255), nullable=True))
    op.add_column('billing_profiles', sa.Column('card_label', sa.String(length=80), nullable=True))
    op.create_table(
        'card_checkouts',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('invoice_id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.String(length=255), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('save_card', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id'),
    )
    op.create_index('ix_card_checkouts_invoice_id', 'card_checkouts', ['invoice_id'])
    op.create_table(
        'stripe_events',
        sa.Column('id', sa.String(length=255), nullable=False),
        sa.Column('type', sa.String(length=100), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('stripe_events')
    op.drop_index('ix_card_checkouts_invoice_id', table_name='card_checkouts')
    op.drop_table('card_checkouts')
    op.drop_column('billing_profiles', 'card_label')
    op.drop_column('billing_profiles', 'card_payment_method')
    op.drop_column('billing_profiles', 'stripe_customer_id')
