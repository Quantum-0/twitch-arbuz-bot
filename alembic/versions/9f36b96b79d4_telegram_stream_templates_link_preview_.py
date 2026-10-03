"""telegram stream templates, link preview and restart detection

Revision ID: 9f36b96b79d4
Revises: 78a98d40f5cf
Create Date: 2026-10-03 19:08:38.217313

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f36b96b79d4'
down_revision: Union[str, Sequence[str], None] = '78a98d40f5cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('telegram_settings', sa.Column('stream_link_preview_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('telegram_settings', sa.Column('stream_offline_message_template', sa.Text(), nullable=True))
    op.add_column('telegram_settings', sa.Column('stream_restart_behavior', sa.String(), server_default='edit', nullable=False))
    op.add_column('telegram_settings', sa.Column('stream_restart_message_template', sa.Text(), nullable=True))
    op.add_column('telegram_settings', sa.Column('last_stream_offline_message_id', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('telegram_settings', 'last_stream_offline_message_id')
    op.drop_column('telegram_settings', 'stream_restart_message_template')
    op.drop_column('telegram_settings', 'stream_restart_behavior')
    op.drop_column('telegram_settings', 'stream_offline_message_template')
    op.drop_column('telegram_settings', 'stream_link_preview_enabled')
