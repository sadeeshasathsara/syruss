"""graph, workspace jobs and evaluations

  * entity_sightings: which identified things appear together in committed records (the
    workspace graph; mirrored into Apache AGE where it is installed)
  * workspace_jobs: the platform's recurring work per workspace (reflection, consolidation,
    budget alerts, graph upkeep)
  * eval_runs / eval_run_items: shadow evaluations against golden documents, which now point at
    the workspace document they were made from

Revision ID: 0004
Revises: 0003
Created: 2026-10-23 23:00:45.298538
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('workspace_jobs',
    sa.Column('template_id', sa.String(length=64), nullable=False),
    sa.Column('job', sa.String(length=32), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_status', sa.String(length=32), nullable=True),
    sa.Column('state', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['template_id'], ['document_templates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('template_id', 'job')
    )
    op.create_index(op.f('ix_workspace_jobs_next_run_at'), 'workspace_jobs', ['next_run_at'], unique=False)
    op.create_index(op.f('ix_workspace_jobs_tenant_id'), 'workspace_jobs', ['tenant_id'], unique=False)
    op.create_table('eval_runs',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('template_id', sa.String(length=64), nullable=False),
    sa.Column('version_id', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('item_count', sa.Integer(), nullable=False),
    sa.Column('metrics', sa.JSON(), nullable=False),
    sa.Column('requested_by', sa.String(length=100), nullable=False),
    sa.Column('requested_by_name', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['template_id'], ['document_templates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['version_id'], ['template_versions.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_eval_runs_created_at'), 'eval_runs', ['created_at'], unique=False)
    op.create_index(op.f('ix_eval_runs_template_id'), 'eval_runs', ['template_id'], unique=False)
    op.create_index(op.f('ix_eval_runs_tenant_id'), 'eval_runs', ['tenant_id'], unique=False)
    op.create_table('entity_sightings',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('template_id', sa.String(length=64), nullable=False),
    sa.Column('document_id', sa.String(length=64), nullable=False),
    sa.Column('subject_key', sa.String(length=400), nullable=False),
    sa.Column('subject_type', sa.String(length=100), nullable=False),
    sa.Column('subject_label', sa.String(length=255), nullable=False),
    sa.Column('context_key', sa.String(length=400), nullable=False),
    sa.Column('context_type', sa.String(length=100), nullable=False),
    sa.Column('context_label', sa.String(length=255), nullable=False),
    sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['template_id'], ['document_templates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_entity_sightings_context', 'entity_sightings', ['template_id', 'context_key'], unique=False)
    op.create_index(op.f('ix_entity_sightings_document_id'), 'entity_sightings', ['document_id'], unique=False)
    op.create_index('ix_entity_sightings_subject', 'entity_sightings', ['template_id', 'subject_key'], unique=False)
    op.create_index(op.f('ix_entity_sightings_tenant_id'), 'entity_sightings', ['tenant_id'], unique=False)
    op.create_table('eval_run_items',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('golden_item_id', sa.String(length=64), nullable=True),
    sa.Column('document_id', sa.String(length=64), nullable=False),
    sa.Column('event_id', sa.String(length=64), nullable=True),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('expected', sa.JSON(), nullable=False),
    sa.Column('answer', sa.JSON(), nullable=True),
    sa.Column('decision', sa.String(length=16), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('scores', sa.JSON(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['golden_item_id'], ['golden_dataset_items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['eval_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_id')
    )
    op.create_index(op.f('ix_eval_run_items_run_id'), 'eval_run_items', ['run_id'], unique=False)
    op.add_column('golden_dataset_items', sa.Column('document_id', sa.String(length=64), nullable=True))
    op.add_column('golden_dataset_items', sa.Column('created_by', sa.String(length=100), nullable=True))
    op.create_index(op.f('ix_golden_dataset_items_document_id'), 'golden_dataset_items', ['document_id'], unique=False)
    op.create_foreign_key('golden_dataset_items_document_id_fkey', 'golden_dataset_items', 'documents', ['document_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    op.drop_constraint('golden_dataset_items_document_id_fkey', 'golden_dataset_items', type_='foreignkey')
    op.drop_index(op.f('ix_golden_dataset_items_document_id'), table_name='golden_dataset_items')
    op.drop_column('golden_dataset_items', 'created_by')
    op.drop_column('golden_dataset_items', 'document_id')
    op.drop_index(op.f('ix_eval_run_items_run_id'), table_name='eval_run_items')
    op.drop_table('eval_run_items')
    op.drop_index(op.f('ix_entity_sightings_tenant_id'), table_name='entity_sightings')
    op.drop_index('ix_entity_sightings_subject', table_name='entity_sightings')
    op.drop_index(op.f('ix_entity_sightings_document_id'), table_name='entity_sightings')
    op.drop_index('ix_entity_sightings_context', table_name='entity_sightings')
    op.drop_table('entity_sightings')
    op.drop_index(op.f('ix_eval_runs_tenant_id'), table_name='eval_runs')
    op.drop_index(op.f('ix_eval_runs_template_id'), table_name='eval_runs')
    op.drop_index(op.f('ix_eval_runs_created_at'), table_name='eval_runs')
    op.drop_table('eval_runs')
    op.drop_index(op.f('ix_workspace_jobs_tenant_id'), table_name='workspace_jobs')
    op.drop_index(op.f('ix_workspace_jobs_next_run_at'), table_name='workspace_jobs')
    op.drop_table('workspace_jobs')
