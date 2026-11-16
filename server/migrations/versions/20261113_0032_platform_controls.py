"""platform controls (maintenance, switches), announcements, and staff notes about organizations

Revision ID: 0032
Revises: 0031
Created: 2026-11-13 10:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0032'
down_revision: Union[str, None] = '0031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_settings',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('updated_by', sa.String(length=100), nullable=False),
        sa.Column('updated_by_name', sa.String(length=255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_table(
        'platform_announcements',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=160), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=True),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=100), nullable=False),
        sa.Column('created_by_name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_platform_announcements_window', 'platform_announcements', ['starts_at', 'ends_at'])
    op.create_table(
        'organization_notes',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('pinned', sa.Boolean(), nullable=False),
        sa.Column('author_id', sa.String(length=100), nullable=False),
        sa.Column('author_name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_organization_notes_tenant_created', 'organization_notes', ['tenant_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_organization_notes_tenant_created', table_name='organization_notes')
    op.drop_table('organization_notes')
    op.drop_index('ix_platform_announcements_window', table_name='platform_announcements')
    op.drop_table('platform_announcements')
    op.drop_table('platform_settings')
