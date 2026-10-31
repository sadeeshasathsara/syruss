"""email verification

One-time codes that confirm an email address before an account is created.

Revision ID: 0012
Revises: 0011
Created: 2026-10-25 18:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0012'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'email_verifications',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('user_id', sa.String(length=100), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'email', name='uq_email_verification'),
    )
    op.create_index('ix_email_verifications_user_id', 'email_verifications', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_email_verifications_user_id', table_name='email_verifications')
    op.drop_table('email_verifications')
