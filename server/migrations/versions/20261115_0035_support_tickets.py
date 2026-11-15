"""support tickets, their messages and files, and staff saved replies

Revision ID: 0035
Revises: 0034
Created: 2026-11-15 10:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0035'
down_revision: Union[str, None] = '0034'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'support_tickets',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('number', sa.String(length=16), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=True),
        sa.Column('template_id', sa.String(length=64), nullable=True),
        sa.Column('opened_by', sa.String(length=100), nullable=True),
        sa.Column('requester_email', sa.String(length=255), nullable=False),
        sa.Column('requester_name', sa.String(length=255), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('category', sa.String(length=16), nullable=False),
        sa.Column('priority', sa.String(length=8), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('channel', sa.String(length=8), nullable=False),
        sa.Column('unverified', sa.Boolean(), nullable=False),
        sa.Column('assignee_id', sa.String(length=100), nullable=True),
        sa.Column('assignee_name', sa.String(length=255), nullable=True),
        sa.Column('related', sa.JSON(), nullable=False),
        sa.Column('first_response_due', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolve_due', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_responded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('breached_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('solved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_customer_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_staff_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('merged_into', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('number'),
    )
    op.create_index('ix_support_tickets_status', 'support_tickets', ['status', 'updated_at'])
    op.create_index('ix_support_tickets_tenant', 'support_tickets', ['tenant_id', 'updated_at'])
    op.create_table(
        'support_messages',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('ticket_id', sa.String(length=64), nullable=False),
        sa.Column('author_kind', sa.String(length=10), nullable=False),
        sa.Column('author_id', sa.String(length=100), nullable=True),
        sa.Column('author_name', sa.String(length=255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('internal', sa.Boolean(), nullable=False),
        sa.Column('via', sa.String(length=8), nullable=False),
        sa.Column('email_message_id', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['ticket_id'], ['support_tickets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_support_messages_ticket', 'support_messages', ['ticket_id', 'created_at'])
    op.create_index('ix_support_messages_email', 'support_messages', ['email_message_id'])
    op.create_table(
        'support_files',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('ticket_id', sa.String(length=64), nullable=False),
        sa.Column('message_id', sa.String(length=64), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=64), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('object_key', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['ticket_id'], ['support_tickets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['message_id'], ['support_messages.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_support_files_ticket_id', 'support_files', ['ticket_id'])
    op.create_index('ix_support_files_message_id', 'support_files', ['message_id'])
    op.create_table(
        'support_saved_replies',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('shortcut', sa.String(length=40), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_by_name', sa.String(length=255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('shortcut'),
    )


def downgrade() -> None:
    op.drop_table('support_saved_replies')
    op.drop_index('ix_support_files_message_id', table_name='support_files')
    op.drop_index('ix_support_files_ticket_id', table_name='support_files')
    op.drop_table('support_files')
    op.drop_index('ix_support_messages_email', table_name='support_messages')
    op.drop_index('ix_support_messages_ticket', table_name='support_messages')
    op.drop_table('support_messages')
    op.drop_index('ix_support_tickets_tenant', table_name='support_tickets')
    op.drop_index('ix_support_tickets_status', table_name='support_tickets')
    op.drop_table('support_tickets')
