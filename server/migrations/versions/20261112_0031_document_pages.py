"""documents with several pages (which page the agent looked at, PDF among accepted types); when a closed organization was purged

Revision ID: 0031
Revises: 0030
Created: 2026-11-12 14:00:00
"""
import json
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0031'
down_revision: Union[str, None] = '0030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IMAGES = {"image/jpeg", "image/png", "image/webp", "image/tiff"}
PDF = "application/pdf"


def upgrade() -> None:
    op.add_column('agent_page_views', sa.Column('page', sa.Integer(), server_default='1', nullable=False))
    op.alter_column('agent_page_views', 'page', server_default=None)
    op.add_column('tenants', sa.Column('purged_at', sa.DateTime(timezone=True), nullable=True))
    # A workspace that accepted every kind of picture accepts PDFs too; one that narrowed its list keeps its choice.
    bind = op.get_bind()
    for template_id, data in bind.execute(sa.text("SELECT template_id, data FROM workspace_settings")).fetchall():
        data = json.loads(data) if isinstance(data, str) else (data or {})
        types = (data.get("documents") or {}).get("allowed_types")
        if isinstance(types, list) and set(types) == IMAGES:
            data["documents"]["allowed_types"] = [*types, PDF]
            bind.execute(sa.text("UPDATE workspace_settings SET data = CAST(:d AS JSON) WHERE template_id = :t"), {"d": json.dumps(data), "t": template_id})


def downgrade() -> None:
    op.drop_column('tenants', 'purged_at')
    op.drop_column('agent_page_views', 'page')
