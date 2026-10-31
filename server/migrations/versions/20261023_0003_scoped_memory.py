"""scoped memory

What Syruss remembers, by scope (workspace, personal, conversation). Where the database has
pgvector, an `embedding` column and an HNSW index rank memories by meaning; everywhere, a full-text
index lets retrieval fall back to words. The column is outside the ORM model on purpose: it only
exists where the extension does (see app/core/migrations.py: include_object).

Revision ID: 0003
Revises: 0002
Created: 2026-10-23 22:14:59.923332
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from app.core.config import settings


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('memories',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('template_id', sa.String(length=64), nullable=False),
    sa.Column('scope', sa.String(length=16), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('user_id', sa.String(length=100), nullable=True),
    sa.Column('conversation_id', sa.String(length=64), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('importance', sa.Float(), nullable=False),
    sa.Column('pinned', sa.Boolean(), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('source', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=100), nullable=False),
    sa.Column('needs_embedding', sa.Boolean(), nullable=False),
    sa.Column('use_count', sa.Integer(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['template_id'], ['document_templates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    # A database adopted by an earlier build may already have this table (see app/core/migrations.py).
    if_not_exists=True,
    )
    op.create_index(op.f('ix_memories_conversation_id'), 'memories', ['conversation_id'], unique=False, if_not_exists=True)
    op.create_index('ix_memories_lookup', 'memories', ['template_id', 'scope', 'user_id'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_memories_template_id'), 'memories', ['template_id'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_memories_tenant_id'), 'memories', ['tenant_id'], unique=False, if_not_exists=True)
    op.execute("CREATE INDEX IF NOT EXISTS ix_memories_content_fts ON memories USING gin (to_tsvector('simple', content))")
    has_vector = op.get_bind().execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar()
    if has_vector:
        op.execute(f"ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding vector({settings.EMBEDDING_DIM})")
        op.execute("CREATE INDEX IF NOT EXISTS ix_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding")
    op.execute("DROP INDEX IF EXISTS ix_memories_content_fts")
    op.drop_index(op.f('ix_memories_tenant_id'), table_name='memories')
    op.drop_index(op.f('ix_memories_template_id'), table_name='memories')
    op.drop_index('ix_memories_lookup', table_name='memories')
    op.drop_index(op.f('ix_memories_conversation_id'), table_name='memories')
    op.drop_table('memories')
