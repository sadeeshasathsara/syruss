"""trusted devices and security events

Browsers a person has signed in from (new ones confirm with an emailed code), and a readable trail of sign-in events.

Revision ID: 0014
Revises: 0013
Created: 2026-10-26 12:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0014'
down_revision: Union[str, None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'trusted_devices',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('user_id', sa.String(length=100), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('user_agent', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('ip', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='pending'),
        sa.Column('code_hash', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('code_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('code_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('remember', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('trusted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('trusted_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'token_hash', name='uq_trusted_device'),
    )
    op.create_index('ix_trusted_devices_user_id', 'trusted_devices', ['user_id'])
    op.create_table(
        'security_events',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('user_id', sa.String(length=100), nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('summary', sa.String(length=300), nullable=False),
        sa.Column('device', sa.String(length=120), nullable=False, server_default=''),
        sa.Column('ip', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_security_events_user_id', 'security_events', ['user_id'])
    op.create_index('ix_security_events_created_at', 'security_events', ['created_at'])


def downgrade() -> None:
    op.drop_table('security_events')
    op.drop_table('trusted_devices')
