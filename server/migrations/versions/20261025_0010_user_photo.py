"""profile photo

A single sign-on provider (Google) can share the person's photo; the profile keeps its address.

Revision ID: 0010
Revises: 0009
Created: 2026-10-25 12:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('avatar_url', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'avatar_url')
