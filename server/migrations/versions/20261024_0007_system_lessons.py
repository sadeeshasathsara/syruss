"""system lessons

Syruss's platform-wide memory: lessons about working in the system itself (seeded at startup by
app.services.system_lessons; agent proposals need an admin's approval).

Revision ID: 0007
Revises: 0006
Created: 2026-10-24 13:39:29.181382
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('system_lessons',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('why', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('importance', sa.Float(), nullable=False),
    sa.Column('template_id', sa.String(length=64), nullable=True),
    sa.Column('event_id', sa.String(length=64), nullable=True),
    sa.Column('times_proposed', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.String(length=100), nullable=False),
    sa.Column('reviewed_by', sa.String(length=100), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['template_id'], ['document_templates.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_system_lessons_status'), 'system_lessons', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_system_lessons_status'), table_name='system_lessons')
    op.drop_table('system_lessons')
