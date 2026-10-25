"""stoppable runs

A chat reply can be stopped: agent events record their conversation, and when a person stopped them.

Revision ID: 0006
Revises: 0005
Created: 2026-10-24 12:01:18.479865
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent_event_outbox', sa.Column('conversation_id', sa.String(length=64), nullable=True))
    op.add_column('agent_event_outbox', sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('agent_event_outbox', sa.Column('cancelled_by', sa.String(length=100), nullable=True))
    op.create_index(op.f('ix_agent_event_outbox_conversation_id'), 'agent_event_outbox', ['conversation_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_agent_event_outbox_conversation_id'), table_name='agent_event_outbox')
    op.drop_column('agent_event_outbox', 'cancelled_by')
    op.drop_column('agent_event_outbox', 'cancelled_at')
    op.drop_column('agent_event_outbox', 'conversation_id')
