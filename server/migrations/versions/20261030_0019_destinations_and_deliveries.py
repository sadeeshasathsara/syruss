"""destinations, deliveries and the revision of an approved record

Approved records are sent to destinations (webhooks, APIs, storage, mail, accounting systems). A delivery is written in the same
transaction that approves the record; a worker sends it, retries it, and keeps every attempt.

Revision ID: 0019
Revises: 0018
Created: 2026-10-30 09:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0019'
down_revision: Union[str, None] = '0018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('extraction_results', sa.Column('revision', sa.Integer(), nullable=False, server_default='0'))
    # Records approved before this existed count as their first approval.
    op.execute("UPDATE extraction_results SET revision = 1 WHERE document_id IN (SELECT id FROM documents WHERE status = 'COMPLETED')")

    op.create_table(
        'destinations',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('tenant_id', sa.String(length=64), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('template_id', sa.String(length=64), sa.ForeignKey('document_templates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active'),
        sa.Column('paused_reason', sa.String(length=300), nullable=True),
        sa.Column('paused_by', sa.String(length=16), nullable=True),
        sa.Column('config', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('secrets', sa.Text(), nullable=True),
        sa.Column('mapping', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('policy', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('consecutive_failures', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_failure_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('created_by', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_destinations_tenant_id', 'destinations', ['tenant_id'])
    op.create_index('ix_destinations_template_id', 'destinations', ['template_id'])

    op.create_table(
        'deliveries',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('tenant_id', sa.String(length=64), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('template_id', sa.String(length=64), sa.ForeignKey('document_templates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('destination_id', sa.String(length=64), sa.ForeignKey('destinations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('document_id', sa.String(length=64), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('event', sa.String(length=32), nullable=False, server_default='document.completed'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('hold_reason', sa.String(length=300), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('last_status_code', sa.Integer(), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('external_ref', sa.String(length=255), nullable=True),
        sa.Column('snapshot', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('payload_hash', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('idempotency_key', sa.String(length=200), nullable=False, unique=True),
        sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('destination_id', 'document_id', 'revision', name='uq_delivery_revision'),
    )
    op.create_index('ix_deliveries_tenant_id', 'deliveries', ['tenant_id'])
    op.create_index('ix_deliveries_destination_id', 'deliveries', ['destination_id'])
    op.create_index('ix_deliveries_document_id', 'deliveries', ['document_id'])
    op.create_index('ix_deliveries_due', 'deliveries', ['status', 'next_attempt_at'])
    op.create_index('ix_deliveries_workspace_time', 'deliveries', ['template_id', 'created_at'])

    op.create_table(
        'delivery_attempts',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('delivery_id', sa.String(length=64), sa.ForeignKey('deliveries.id', ondelete='CASCADE'), nullable=False),
        sa.Column('attempt_no', sa.Integer(), nullable=False),
        sa.Column('ok', sa.Boolean(), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Float(), nullable=True),
        sa.Column('request', sa.Text(), nullable=True),
        sa.Column('response', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_delivery_attempts_delivery_id', 'delivery_attempts', ['delivery_id'])


def downgrade() -> None:
    op.drop_table('delivery_attempts')
    op.drop_table('deliveries')
    op.drop_table('destinations')
    op.drop_column('extraction_results', 'revision')
