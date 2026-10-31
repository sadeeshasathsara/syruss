"""extraction log

What happened to each document, in plain words, for everyone in a workspace to read.

Revision ID: 0013
Revises: 0012
Created: 2026-10-26 09:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'extraction_log',
        sa.Column('seq', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('id', sa.String(length=64), nullable=False, unique=True),
        sa.Column('tenant_id', sa.String(length=64), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('template_id', sa.String(length=64), sa.ForeignKey('document_templates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('document_id', sa.String(length=64), nullable=True),
        sa.Column('document_name', sa.String(length=255), nullable=True),
        sa.Column('stage', sa.String(length=24), nullable=False),
        sa.Column('level', sa.String(length=10), nullable=False, server_default='info'),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('actor', sa.String(length=100), nullable=False, server_default='system'),
        sa.Column('details', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_extraction_log_tenant_id', 'extraction_log', ['tenant_id'])
    op.create_index('ix_extraction_log_document_id', 'extraction_log', ['document_id'])
    op.create_index('ix_extraction_log_created_at', 'extraction_log', ['created_at'])
    op.create_index('ix_extraction_log_workspace_seq', 'extraction_log', ['template_id', 'seq'])


def downgrade() -> None:
    op.drop_table('extraction_log')
