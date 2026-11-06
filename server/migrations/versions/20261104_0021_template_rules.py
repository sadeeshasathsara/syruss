"""template rules

Checks written in the rule language that run on every record after a document is read. People write them, or Syruss
suggests them; they live beside the template rather than in its versions, so adding one doesn't need a new version.

Revision ID: 0021
Revises: 0020
Created: 2026-11-04 12:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0021'
down_revision: Union[str, None] = '0020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'template_rules',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('tenant_id', sa.String(length=64), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('template_id', sa.String(length=64), sa.ForeignKey('document_templates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('scope', sa.String(length=100), nullable=False, server_default='document'),
        sa.Column('check', sa.Text(), nullable=False),
        sa.Column('when', sa.Text(), nullable=False, server_default=''),
        sa.Column('severity', sa.String(length=16), nullable=False, server_default='error'),
        sa.Column('message', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active'),
        sa.Column('created_by', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('created_by_kind', sa.String(length=16), nullable=False, server_default='person'),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('source_document_id', sa.String(length=64), nullable=True),
        sa.Column('source_conversation_id', sa.String(length=64), nullable=True),
        sa.Column('checked_against_version_id', sa.String(length=64), nullable=True),
        sa.Column('broken_reason', sa.Text(), nullable=True),
        sa.Column('last_dry_run', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_template_rules_tenant_id', 'template_rules', ['tenant_id'])
    op.create_index('ix_template_rules_template_id', 'template_rules', ['template_id'])
    op.create_index('ix_template_rules_status', 'template_rules', ['status'])


def downgrade() -> None:
    op.drop_index('ix_template_rules_status', table_name='template_rules')
    op.drop_index('ix_template_rules_template_id', table_name='template_rules')
    op.drop_index('ix_template_rules_tenant_id', table_name='template_rules')
    op.drop_table('template_rules')
