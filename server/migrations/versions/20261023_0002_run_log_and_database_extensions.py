"""run log and database extensions

The agent's runs record where they came from (origin, who, which conversation) and what they cost.
pgvector and Apache AGE are enabled where the database has them (see bridge/postgres/Dockerfile);
a database without them still upgrades, and the features that need them stay off.

Revision ID: 0002
Revises: 0001
Created: 2026-10-23 21:38:45.581961
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EXTENSIONS = ("vector", "age")


def _available(name: str) -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = :n"), {"n": name}).scalar())


def upgrade() -> None:
    for name in EXTENSIONS:
        if _available(name):
            op.execute(f'CREATE EXTENSION IF NOT EXISTS "{name}"')

    # Existing runs predate origins: they were the platform's own work. Each step tolerates being
    # already done, so a database adopted with today's table shapes upgrades cleanly too.
    op.add_column('agent_execution_traces', sa.Column('run_id', sa.String(length=64), nullable=True), if_not_exists=True)
    op.add_column('agent_execution_traces', sa.Column('event_id', sa.String(length=64), nullable=True), if_not_exists=True)
    op.add_column('agent_execution_traces', sa.Column('origin_kind', sa.String(length=16), nullable=False, server_default='system'), if_not_exists=True)
    op.add_column('agent_execution_traces', sa.Column('origin_source', sa.String(length=16), nullable=False, server_default='system'), if_not_exists=True)
    op.add_column('agent_execution_traces', sa.Column('actor_type', sa.String(length=16), nullable=False, server_default='system'), if_not_exists=True)
    op.add_column('agent_execution_traces', sa.Column('actor_id', sa.String(length=100), nullable=True), if_not_exists=True)
    op.add_column('agent_execution_traces', sa.Column('actor_name', sa.String(length=255), nullable=True), if_not_exists=True)
    op.add_column('agent_execution_traces', sa.Column('conversation_id', sa.String(length=64), nullable=True), if_not_exists=True)
    op.add_column('agent_execution_traces', sa.Column('model_calls', sa.Integer(), nullable=False, server_default='0'), if_not_exists=True)
    op.add_column('agent_execution_traces', sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'), if_not_exists=True)
    op.add_column('agent_execution_traces', sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'), if_not_exists=True)
    op.add_column('agent_execution_traces', sa.Column('cost_usd', sa.Float(), nullable=True), if_not_exists=True)
    op.create_index(op.f('ix_agent_execution_traces_actor_id'), 'agent_execution_traces', ['actor_id'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_agent_execution_traces_conversation_id'), 'agent_execution_traces', ['conversation_id'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_agent_execution_traces_event_id'), 'agent_execution_traces', ['event_id'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_agent_execution_traces_origin_kind'), 'agent_execution_traces', ['origin_kind'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_agent_execution_traces_run_id'), 'agent_execution_traces', ['run_id'], unique=False, if_not_exists=True)
    op.create_index('ix_agent_runs_workspace_started', 'agent_execution_traces', ['template_id', 'started_at'], unique=False, if_not_exists=True)


def downgrade() -> None:
    op.drop_index('ix_agent_runs_workspace_started', table_name='agent_execution_traces')
    op.drop_index(op.f('ix_agent_execution_traces_run_id'), table_name='agent_execution_traces')
    op.drop_index(op.f('ix_agent_execution_traces_origin_kind'), table_name='agent_execution_traces')
    op.drop_index(op.f('ix_agent_execution_traces_event_id'), table_name='agent_execution_traces')
    op.drop_index(op.f('ix_agent_execution_traces_conversation_id'), table_name='agent_execution_traces')
    op.drop_index(op.f('ix_agent_execution_traces_actor_id'), table_name='agent_execution_traces')
    for column in ('cost_usd', 'output_tokens', 'input_tokens', 'model_calls', 'conversation_id', 'actor_name',
                   'actor_id', 'actor_type', 'origin_source', 'origin_kind', 'event_id', 'run_id'):
        op.drop_column('agent_execution_traces', column)
    # The extensions stay: other data may use them.
