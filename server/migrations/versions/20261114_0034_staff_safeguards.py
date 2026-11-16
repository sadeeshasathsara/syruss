"""staff safeguards: a second owner's approval, offboarding and access reviews, customers deciding on support access

Revision ID: 0034
Revises: 0033
Created: 2026-11-14 15:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0034'
down_revision: Union[str, None] = '0033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_approvals',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('target_type', sa.String(length=32), nullable=False),
        sa.Column('target_id', sa.String(length=100), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('requested_by', sa.String(length=100), nullable=False),
        sa.Column('requested_by_name', sa.String(length=255), nullable=False),
        sa.Column('requested_by_email', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('decided_by', sa.String(length=100), nullable=True),
        sa.Column('decided_by_name', sa.String(length=255), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_platform_approvals_status', 'platform_approvals', ['status', 'created_at'])
    op.add_column('platform_staff', sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('platform_staff', sa.Column('offboard_warned_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('platform_staff', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('platform_staff', sa.Column('reviewed_by_name', sa.String(length=255), nullable=True))
    op.add_column('platform_access_grants', sa.Column('ended_by_name', sa.String(length=255), nullable=True))
    op.add_column('tenants', sa.Column('support_access', sa.Boolean(), server_default=sa.text('true'), nullable=False))


def downgrade() -> None:
    op.drop_column('tenants', 'support_access')
    op.drop_column('platform_access_grants', 'ended_by_name')
    for column in ('reviewed_by_name', 'reviewed_at', 'offboard_warned_at', 'last_active_at'):
        op.drop_column('platform_staff', column)
    op.drop_index('ix_platform_approvals_status', table_name='platform_approvals')
    op.drop_table('platform_approvals')
