"""conversation mode

What a chat is for, fixed when it starts: general, rules (written from Template > Rules > New) or screens (written from
Analytics). Every message of the chat is answered the same way, instead of each one being guessed from its words.

Revision ID: 0022
Revises: 0021
Created: 2026-11-05 12:00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0022'
down_revision: Union[str, None] = '0021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('mode', sa.String(length=16), server_default='general', nullable=False))
    # Chats already started from those pages keep what they were for.
    op.execute("UPDATE conversations SET mode = context_metadata->>'mode' "
               "WHERE kind = 'chat' AND context_metadata->>'mode' IN ('rules', 'screens')")


def downgrade() -> None:
    op.drop_column('conversations', 'mode')
