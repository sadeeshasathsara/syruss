"""record_objects: what each committed record contains, object by object (object and thing profiles)

The rows are written by the server: for records committed from now on as they are committed, and for those already
committed by the scheduler's "objects" job, which fills each workspace on its first pass.

Revision ID: 0039
Revises: 0038
"""
import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "record_objects",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id", sa.String(64), sa.ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_type", sa.String(100), nullable=False),
        sa.Column("thing_key", sa.String(400), nullable=True),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("values", sa.JSON(), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "object_type", "thing_key", name="uq_record_objects_document_object_thing"),
    )
    op.create_index("ix_record_objects_tenant_id", "record_objects", ["tenant_id"])
    op.create_index("ix_record_objects_document_id", "record_objects", ["document_id"])
    op.create_index("ix_record_objects_object", "record_objects", ["template_id", "object_type", "seen_at"])
    op.create_index("ix_record_objects_thing", "record_objects", ["template_id", "thing_key"])


def downgrade() -> None:
    op.drop_table("record_objects")
