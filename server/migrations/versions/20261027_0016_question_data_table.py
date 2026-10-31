"""a table of what the agent read, on its questions

A flag can carry the data it is about (the rows read from a sheet) so a person sees a table, not a paragraph.

Revision ID: 0016
Revises: 0015
Created: 2026-10-27 12:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0016'
down_revision: Union[str, None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent_questions', sa.Column('data_table', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('agent_questions', 'data_table')
