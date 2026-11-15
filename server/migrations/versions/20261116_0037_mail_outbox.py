"""mail on its way to the mail service

Revision ID: 0037
Revises: 0036
"""
import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_outbox",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("to", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mail_outbox_status", "mail_outbox", ["status"])
    op.create_index("ix_mail_outbox_created_at", "mail_outbox", ["created_at"])
    op.create_index("ix_mail_outbox_published_at", "mail_outbox", ["published_at"])


def downgrade() -> None:
    op.drop_table("mail_outbox")
