"""page reads, agent conversations and flags

  * agent_page_views: every page the agent views, so reading pages can be capped
  * conversations: kind (chat | agent), and for conversations Syruss opens: status, audience,
    resolution, and agent_state (its checkpoint until the conversation is resolved)
  * agent_questions: answer_type (text | single | multiple) and the choices picked

Revision ID: 0005
Revises: 0004
Created: 2026-10-24 03:28:45.800699
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('agent_page_views',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('template_id', sa.String(length=64), nullable=False),
    sa.Column('event_id', sa.String(length=64), nullable=False),
    sa.Column('document_id', sa.String(length=64), nullable=False),
    sa.Column('actor_id', sa.String(length=100), nullable=True),
    sa.Column('purpose', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['template_id'], ['document_templates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_agent_page_views_event', 'agent_page_views', ['event_id', 'document_id'], unique=False)
    op.create_index('ix_agent_page_views_person_day', 'agent_page_views', ['template_id', 'actor_id', 'created_at'], unique=False)
    op.add_column('agent_questions', sa.Column('answer_type', sa.String(length=16), server_default='text', nullable=False))
    op.add_column('agent_questions', sa.Column('answer_choices', sa.JSON(), nullable=True))
    op.add_column('agent_questions', sa.Column('answer_other', sa.Text(), nullable=True))
    op.add_column('conversations', sa.Column('kind', sa.String(length=16), server_default='chat', nullable=False))
    op.add_column('conversations', sa.Column('status', sa.String(length=16), nullable=True))
    op.add_column('conversations', sa.Column('audience', sa.String(length=16), nullable=True))
    op.add_column('conversations', sa.Column('agent_state', sa.JSON(), nullable=True))
    op.add_column('conversations', sa.Column('resolution', sa.Text(), nullable=True))
    op.add_column('conversations', sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_conversations_listing', 'conversations', ['template_id', 'kind', 'updated_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_conversations_listing', table_name='conversations')
    op.drop_column('conversations', 'resolved_at')
    op.drop_column('conversations', 'resolution')
    op.drop_column('conversations', 'agent_state')
    op.drop_column('conversations', 'audience')
    op.drop_column('conversations', 'status')
    op.drop_column('conversations', 'kind')
    op.drop_column('agent_questions', 'answer_other')
    op.drop_column('agent_questions', 'answer_choices')
    op.drop_column('agent_questions', 'answer_type')
    op.drop_index('ix_agent_page_views_person_day', table_name='agent_page_views')
    op.drop_index('ix_agent_page_views_event', table_name='agent_page_views')
    op.drop_table('agent_page_views')
