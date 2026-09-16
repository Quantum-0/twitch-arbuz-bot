"""add clips_delivery to telegram_settings

Revision ID: 3c9ce04fdc36
Revises: f7f7298843e9
Create Date: 2026-09-16 02:58:29.396933

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c9ce04fdc36'
down_revision: Union[str, Sequence[str], None] = 'f7f7298843e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('telegram_settings', sa.Column('clips_delivery', sa.String(), server_default='link', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('telegram_settings', 'clips_delivery')
