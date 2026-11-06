"""runs saved while they run, what each run changed, and staff access to customer content

A run is written when it starts and after every step, so a crash leaves its trail and the platform can see what is
running now. Log and audit entries the agent writes name the event (run) that wrote them. Platform staff open a
customer's content only through a grant: one organization, a reason, a time limit.

Revision ID: 0024
Revises: 0023
Created: 2026-11-07 09:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0024'
down_revision: Union[str, None] = '0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent_execution_traces', sa.Column('attempt', sa.Integer(), server_default='1', nullable=False))
    op.add_column('agent_execution_traces', sa.Column('queued_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('agent_execution_traces', sa.Column('last_step_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('agent_execution_traces', sa.Column('current_step', sa.Integer(), nullable=True))
    op.add_column('agent_execution_traces', sa.Column('current_tool', sa.String(length=100), nullable=True))
    op.create_index('ix_agent_runs_status_started', 'agent_execution_traces', ['status', 'started_at'])
    op.create_index('ix_agent_runs_tenant_started', 'agent_execution_traces', ['tenant_id', 'started_at'])

    op.add_column('extraction_log', sa.Column('event_id', sa.String(length=64), nullable=True))
    op.create_index('ix_extraction_log_event_id', 'extraction_log', ['event_id'])
    op.add_column('audit_logs', sa.Column('event_id', sa.String(length=64), nullable=True))
    op.create_index('ix_audit_logs_event_id', 'audit_logs', ['event_id'])

    op.create_table(
        'platform_access_grants',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('staff_id', sa.String(length=100), nullable=False),
        sa.Column('staff_name', sa.String(length=255), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_platform_access_staff_tenant', 'platform_access_grants', ['staff_id', 'tenant_id', 'expires_at'])


def downgrade() -> None:
    op.drop_index('ix_platform_access_staff_tenant', table_name='platform_access_grants')
    op.drop_table('platform_access_grants')
    op.drop_index('ix_audit_logs_event_id', table_name='audit_logs')
    op.drop_column('audit_logs', 'event_id')
    op.drop_index('ix_extraction_log_event_id', table_name='extraction_log')
    op.drop_column('extraction_log', 'event_id')
    op.drop_index('ix_agent_runs_tenant_started', table_name='agent_execution_traces')
    op.drop_index('ix_agent_runs_status_started', table_name='agent_execution_traces')
    for column in ('current_tool', 'current_step', 'last_step_at', 'queued_at', 'attempt'):
        op.drop_column('agent_execution_traces', column)
