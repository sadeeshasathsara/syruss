"""intake channels, scoped intake keys, and where each document came from

Other systems send documents in through channels. Each channel has its own keys (limits, allowed addresses, test or live), and every
document records the channel and the sender's own reference for it.

Revision ID: 0018
Revises: 0017
Created: 2026-10-29 09:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0018'
down_revision: Union[str, None] = '0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'intake_channels',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('tenant_id', sa.String(length=64), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('template_id', sa.String(length=64), sa.ForeignKey('document_templates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('slug', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False, server_default='api'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active'),
        sa.Column('config', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_by', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('template_id', 'slug', name='uq_intake_channel_slug'),
    )
    op.create_index('ix_intake_channels_tenant_id', 'intake_channels', ['tenant_id'])
    op.create_index('ix_intake_channels_template_id', 'intake_channels', ['template_id'])

    op.create_table(
        'intake_events',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.String(length=64), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('template_id', sa.String(length=64), sa.ForeignKey('document_templates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel_id', sa.String(length=64), sa.ForeignKey('intake_channels.id', ondelete='SET NULL'), nullable=True),
        sa.Column('outcome', sa.String(length=16), nullable=False),
        sa.Column('reason', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('filename', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('external_id', sa.String(length=200), nullable=True),
        sa.Column('document_id', sa.String(length=64), nullable=True),
        sa.Column('sender', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('ip', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('key_id', sa.String(length=64), nullable=True),
        sa.Column('via', sa.String(length=16), nullable=False, server_default='api'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_intake_events_tenant_id', 'intake_events', ['tenant_id'])
    op.create_index('ix_intake_events_channel_id', 'intake_events', ['channel_id'])
    op.create_index('ix_intake_events_created_at', 'intake_events', ['created_at'])
    op.create_index('ix_intake_events_workspace_time', 'intake_events', ['template_id', 'created_at'])

    op.create_table(
        'idempotency_records',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('tenant_id', sa.String(length=64), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel_id', sa.String(length=64), sa.ForeignKey('intake_channels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('key', sa.String(length=120), nullable=False),
        sa.Column('request_hash', sa.String(length=64), nullable=False),
        sa.Column('document_id', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('channel_id', 'key', name='uq_idempotency_key'),
    )
    op.create_index('ix_idempotency_records_tenant_id', 'idempotency_records', ['tenant_id'])

    op.create_table(
        'intake_uploads',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('tenant_id', sa.String(length=64), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('template_id', sa.String(length=64), sa.ForeignKey('document_templates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel_id', sa.String(length=64), sa.ForeignKey('intake_channels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('key_id', sa.String(length=64), nullable=True),
        sa.Column('object_key', sa.String(length=512), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('declared_type', sa.String(length=64), nullable=False),
        sa.Column('max_bytes', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(length=200), nullable=True),
        sa.Column('sender_metadata', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('process', sa.Boolean(), nullable=True),
        sa.Column('state', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('document_id', sa.String(length=64), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('notes', sa.Text(), nullable=True),
    )
    op.create_index('ix_intake_uploads_tenant_id', 'intake_uploads', ['tenant_id'])
    op.create_index('ix_intake_uploads_template_id', 'intake_uploads', ['template_id'])

    op.add_column('workspace_api_keys', sa.Column('channel_id', sa.String(length=64), sa.ForeignKey('intake_channels.id', ondelete='CASCADE'), nullable=True))
    op.create_index('ix_workspace_api_keys_channel_id', 'workspace_api_keys', ['channel_id'])
    op.add_column('workspace_api_keys', sa.Column('mode', sa.String(length=8), nullable=False, server_default='live'))
    op.add_column('workspace_api_keys', sa.Column('ip_allowlist', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column('workspace_api_keys', sa.Column('rate_limit_per_minute', sa.Integer(), nullable=False, server_default='60'))
    op.add_column('workspace_api_keys', sa.Column('daily_quota', sa.Integer(), nullable=True))
    op.add_column('workspace_api_keys', sa.Column('replaced_by', sa.String(length=64), nullable=True))

    op.add_column('documents', sa.Column('channel_id', sa.String(length=64), sa.ForeignKey('intake_channels.id', ondelete='SET NULL'), nullable=True))
    op.create_index('ix_documents_channel_id', 'documents', ['channel_id'])
    op.add_column('documents', sa.Column('external_id', sa.String(length=200), nullable=True))
    op.add_column('documents', sa.Column('source', sa.JSON(), nullable=True))
    op.add_column('documents', sa.Column('sender_metadata', sa.JSON(), nullable=True))
    op.add_column('documents', sa.Column('is_test', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index('uq_document_external_id', 'documents', ['channel_id', 'external_id'], unique=True, postgresql_where=sa.text('external_id IS NOT NULL'))


def downgrade() -> None:
    op.drop_index('uq_document_external_id', table_name='documents')
    for column in ('is_test', 'sender_metadata', 'source', 'external_id'):
        op.drop_column('documents', column)
    op.drop_index('ix_documents_channel_id', table_name='documents')
    op.drop_column('documents', 'channel_id')
    for column in ('replaced_by', 'daily_quota', 'rate_limit_per_minute', 'ip_allowlist', 'mode'):
        op.drop_column('workspace_api_keys', column)
    op.drop_index('ix_workspace_api_keys_channel_id', table_name='workspace_api_keys')
    op.drop_column('workspace_api_keys', 'channel_id')
    op.drop_table('intake_uploads')
    op.drop_table('idempotency_records')
    op.drop_table('intake_events')
    op.drop_table('intake_channels')
