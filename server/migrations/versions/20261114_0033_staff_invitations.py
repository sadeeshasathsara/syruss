"""platform staff join by emailed invitation

Revision ID: 0033
Revises: 0032
Created: 2026-11-14 09:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0033'
down_revision: Union[str, None] = '0032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_staff_invitations',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('invited_by', sa.String(length=100), nullable=False),
        sa.Column('invited_by_name', sa.String(length=255), nullable=False),
        sa.Column('invited_by_email', sa.String(length=255), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('accepted_by', sa.String(length=100), nullable=True),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('email_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_platform_staff_invitations_email', 'platform_staff_invitations', ['email'])


def downgrade() -> None:
    op.drop_index('ix_platform_staff_invitations_email', table_name='platform_staff_invitations')
    op.drop_table('platform_staff_invitations')
