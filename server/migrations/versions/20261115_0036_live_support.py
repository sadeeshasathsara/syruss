"""Live support in a chat: the chat's support state and who on the staff is online.

Revision ID: 0036
Revises: 0035
"""
import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("support_state", sa.String(16), nullable=True))
    op.add_column("conversations", sa.Column("support", sa.JSON(), nullable=True))
    op.create_index("ix_conversations_support_state", "conversations", ["support_state"])
    op.create_table(
        "support_presence",
        sa.Column("user_id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("support_presence")
    op.drop_index("ix_conversations_support_state", table_name="conversations")
    op.drop_column("conversations", "support")
    op.drop_column("conversations", "support_state")
