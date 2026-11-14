"""document identified key

The identification key the agent read off a document before its record was saved, so a document is named by it
in lists, notifications and questions from the moment it is read (a saved record's own values take over).

Revision ID: 0020
Revises: 0019
Created: 2026-11-03 12:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0020'
down_revision: Union[str, None] = '0019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('documents', sa.Column('identified_key', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('documents', 'identified_key')
