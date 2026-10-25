"""accounts, organizations and workspace roles

Keycloak proves who someone is; these tables hold what they may do. Adds accounts (users), organization
membership and invitations, workspace roles (the five built-in roles are seeded and workspaces may add
their own), and single-use WebSocket tickets. A member's role becomes a reference to workspace_roles.

Revision ID: 0009
Revises: 0008
Created: 2026-10-25 09:00:00
"""
from datetime import datetime, timezone
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALL = ["documents.view", "documents.upload", "documents.review", "knowledge.view", "knowledge.manage", "template.publish",
       "agent.chat", "members.view", "members.manage", "roles.manage", "integrations.manage", "audit.view",
       "workspace.manage", "billing.manage", "workspace.delete"]
VIEW = ["documents.view", "knowledge.view", "agent.chat", "members.view"]
SYSTEM_ROLES = [
    ("owner", "Owner", "Everything, including billing and deleting the workspace.", ALL),
    ("admin", "Admin", "Runs the workspace: settings, members, roles, integrations, the template and its knowledge.",
     [p for p in ALL if p not in ("workspace.delete", "billing.manage")]),
    ("editor", "Editor", "Runs the work: uploads documents, reviews, manages knowledge and publishes template changes.",
     VIEW + ["template.publish", "knowledge.manage", "documents.upload", "documents.review"]),
    ("reviewer", "Reviewer", "Reviews documents and answers the agent's questions.", VIEW + ["documents.review"]),
    ("viewer", "Viewer", "Sees everything in the workspace, changes nothing.", VIEW),
]


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.String(length=100), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('email_verified', sa.Boolean(), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=False),
        sa.Column('phone', sa.String(length=32), nullable=True),
        sa.Column('job_title', sa.String(length=255), nullable=True),
        sa.Column('timezone', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('onboarded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    op.create_table(
        'org_members',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=100), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('invited_by', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'user_id', name='uq_org_member'),
    )
    op.create_index('ix_org_members_tenant_id', 'org_members', ['tenant_id'])
    op.create_index('ix_org_members_user_id', 'org_members', ['user_id'])

    op.create_table(
        'org_invitations',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('invited_by', sa.String(length=100), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('accepted_by', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_org_invitations_tenant_id', 'org_invitations', ['tenant_id'])

    roles = op.create_table(
        'workspace_roles',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=True),
        sa.Column('template_id', sa.String(length=64), nullable=True),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('is_system', sa.Boolean(), nullable=False),
        sa.Column('permissions', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['template_id'], ['document_templates.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_workspace_roles_tenant_id', 'workspace_roles', ['tenant_id'])
    op.create_index('ix_workspace_roles_template_id', 'workspace_roles', ['template_id'])
    now = datetime.now(timezone.utc)
    op.bulk_insert(roles, [
        {"id": key, "tenant_id": None, "template_id": None, "name": name, "description": description, "is_system": True,
         "permissions": sorted(perms), "created_by": None, "created_at": now, "updated_at": now}
        for key, name, description, perms in SYSTEM_ROLES
    ])

    op.create_table(
        'ws_tickets',
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=100), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('channel', sa.String(length=255), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('token_hash'),
    )
    op.create_index('ix_ws_tickets_expires_at', 'ws_tickets', ['expires_at'])

    # A member's or invitation's role now references workspace_roles; the existing keys are the built-in ids.
    for table in ('workspace_members', 'workspace_invitations'):
        op.alter_column(table, 'role', existing_type=sa.String(length=32), type_=sa.String(length=64), existing_nullable=False)
        op.create_foreign_key(f'{table}_role_fkey', table, 'workspace_roles', ['role'], ['id'])
    op.create_index('ix_workspace_members_role', 'workspace_members', ['role'])


def downgrade() -> None:
    op.drop_index('ix_workspace_members_role', table_name='workspace_members')
    for table in ('workspace_invitations', 'workspace_members'):
        op.drop_constraint(f'{table}_role_fkey', table, type_='foreignkey')
        op.alter_column(table, 'role', existing_type=sa.String(length=64), type_=sa.String(length=32), existing_nullable=False)
    op.drop_index('ix_ws_tickets_expires_at', table_name='ws_tickets')
    op.drop_table('ws_tickets')
    op.drop_index('ix_workspace_roles_template_id', table_name='workspace_roles')
    op.drop_index('ix_workspace_roles_tenant_id', table_name='workspace_roles')
    op.drop_table('workspace_roles')
    op.drop_index('ix_org_invitations_tenant_id', table_name='org_invitations')
    op.drop_table('org_invitations')
    op.drop_index('ix_org_members_user_id', table_name='org_members')
    op.drop_index('ix_org_members_tenant_id', table_name='org_members')
    op.drop_table('org_members')
    op.drop_index('ix_users_email', table_name='users')
    op.drop_table('users')
