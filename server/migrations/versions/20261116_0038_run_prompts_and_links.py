"""what each run was given (its prompt), and each document's runs linked in order

Revision ID: 0038
Revises: 0037
"""
import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_prompts",
        sa.Column("hash", sa.String(64), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("agent_execution_traces", sa.Column("system_prompt_hash", sa.String(64), nullable=True))
    op.add_column("agent_execution_traces", sa.Column("briefing", sa.Text(), nullable=True))
    op.add_column("agent_execution_traces", sa.Column("tools", sa.JSON(), nullable=True))
    op.add_column("agent_execution_traces", sa.Column("previous_run_id", sa.String(64), nullable=True))
    op.create_index("ix_agent_execution_traces_system_prompt_hash", "agent_execution_traces", ["system_prompt_hash"])
    op.create_index("ix_agent_execution_traces_previous_run_id", "agent_execution_traces", ["previous_run_id"])
    # Runs already recorded: each document's runs linked in the order they started
    op.execute("""
        UPDATE agent_execution_traces AS t SET previous_run_id = ordered.previous
        FROM (SELECT id, lag(id) OVER (PARTITION BY document_id ORDER BY started_at, id) AS previous
              FROM agent_execution_traces WHERE document_id IS NOT NULL) AS ordered
        WHERE t.id = ordered.id AND ordered.previous IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_agent_execution_traces_previous_run_id", table_name="agent_execution_traces")
    op.drop_index("ix_agent_execution_traces_system_prompt_hash", table_name="agent_execution_traces")
    for column in ("previous_run_id", "tools", "briefing", "system_prompt_hash"):
        op.drop_column("agent_execution_traces", column)
    op.drop_table("agent_prompts")
