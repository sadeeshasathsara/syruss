"""invitation email

How an invitation's email went (so people can see "sent" or "couldn't send" and try again), and an optional
personal note from the inviter.

Revision ID: 0011
Revises: 0010
Created: 2026-10-25 15:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ('workspace_invitations', 'org_invitations')


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column('email_status', sa.String(length=16), nullable=False, server_default='NOT_SENT'))
        op.add_column(table, sa.Column('email_sent_at', sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column('email_error', sa.String(length=300), nullable=True))
        op.add_column(table, sa.Column('note', sa.String(length=500), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        for column in ('note', 'email_error', 'email_sent_at', 'email_status'):
            op.drop_column(table, column)
