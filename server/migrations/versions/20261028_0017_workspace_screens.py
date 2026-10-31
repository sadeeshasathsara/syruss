"""custom screens

Saved tables over a workspace's records: the columns, calculations, filters and grouping a person or the agent chose.

Revision ID: 0017
Revises: 0016
Created: 2026-10-28 09:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0017'
down_revision: Union[str, None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workspace_screens',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('tenant_id', sa.String(length=64), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('template_id', sa.String(length=64), sa.ForeignKey('document_templates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('slug', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('definition', sa.JSON(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active'),
        sa.Column('created_by', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('created_by_kind', sa.String(length=16), nullable=False, server_default='person'),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('template_id', 'slug', name='uq_screen_slug'),
    )
    op.create_index('ix_workspace_screens_tenant_id', 'workspace_screens', ['tenant_id'])
    op.create_index('ix_workspace_screens_template_id', 'workspace_screens', ['template_id'])


def downgrade() -> None:
    op.drop_table('workspace_screens')
