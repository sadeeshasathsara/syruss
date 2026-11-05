"""platform staff, their audit log, and why an organization is shut

Platform staff and their roles (the system admin app), an append-only log of what they do, and a status on
organizations so a suspended one (by staff) is told apart from a closed one (by its owner).

Revision ID: 0023
Revises: 0022
Created: 2026-11-06 09:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0023'
down_revision: Union[str, None] = '0022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('status', sa.String(length=16), server_default='active', nullable=False))
    op.add_column('tenants', sa.Column('status_reason', sa.Text(), nullable=True))
    op.add_column('tenants', sa.Column('status_changed_at', sa.DateTime(timezone=True), nullable=True))
    # Until now an organization only stopped opening when its owner closed it.
    op.execute("UPDATE tenants SET status = 'closed', status_reason = 'Closed by its owner' WHERE NOT is_active")

    op.create_table(
        'platform_staff',
        sa.Column('user_id', sa.String(length=100), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('added_by', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )
    op.create_table(
        'platform_audit_log',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('actor_id', sa.String(length=100), nullable=False),
        sa.Column('actor_name', sa.String(length=255), nullable=False),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('target_type', sa.String(length=32), nullable=False),
        sa.Column('target_id', sa.String(length=100), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_platform_audit_log_actor_id', 'platform_audit_log', ['actor_id'])
    op.create_index('ix_platform_audit_log_action', 'platform_audit_log', ['action'])
    op.create_index('ix_platform_audit_log_created_at', 'platform_audit_log', ['created_at'])
    op.create_index('ix_platform_audit_tenant_created', 'platform_audit_log', ['tenant_id', 'created_at'])
    # The log can only grow: rows are never changed or removed, whoever asks.
    op.execute("""
        CREATE FUNCTION platform_audit_log_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'platform_audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER platform_audit_log_append_only BEFORE UPDATE OR DELETE ON platform_audit_log
        FOR EACH ROW EXECUTE FUNCTION platform_audit_log_append_only()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS platform_audit_log_append_only ON platform_audit_log")
    op.execute("DROP FUNCTION IF EXISTS platform_audit_log_append_only()")
    op.drop_index('ix_platform_audit_tenant_created', table_name='platform_audit_log')
    op.drop_index('ix_platform_audit_log_created_at', table_name='platform_audit_log')
    op.drop_index('ix_platform_audit_log_action', table_name='platform_audit_log')
    op.drop_index('ix_platform_audit_log_actor_id', table_name='platform_audit_log')
    op.drop_table('platform_audit_log')
    op.drop_table('platform_staff')
    op.drop_column('tenants', 'status_changed_at')
    op.drop_column('tenants', 'status_reason')
    op.drop_column('tenants', 'status')
