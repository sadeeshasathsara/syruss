"""flag conversations

Every flag (agent question) gets its own chat thread: the answer is posted there and Syruss follows
up there until it resolves it.

Revision ID: 0008
Revises: 0007
Created: 2026-10-24 14:07:57.574837
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent_questions', sa.Column('conversation_id', sa.String(length=64), nullable=True))
    op.create_foreign_key('agent_questions_conversation_id_fkey', 'agent_questions', 'conversations', ['conversation_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('agent_questions_conversation_id_fkey', 'agent_questions', type_='foreignkey')
    op.drop_column('agent_questions', 'conversation_id')
