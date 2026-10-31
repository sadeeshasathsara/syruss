"""remember the organization a person last used

Signing in opens the organization you were last in, not the oldest one you belong to.

Revision ID: 0015
Revises: 0014
Created: 2026-10-27 09:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0015'
down_revision: Union[str, None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('last_org_id', sa.String(length=64), nullable=True))
    op.create_foreign_key('fk_users_last_org', 'users', 'tenants', ['last_org_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_users_last_org', 'users', type_='foreignkey')
    op.drop_column('users', 'last_org_id')
