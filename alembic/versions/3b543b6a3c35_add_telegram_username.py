"""add telegram_username

Revision ID: 3b543b6a3c35
Revises: 052f4f454bc0
Create Date: 2026-10-11 04:45:37.096991

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3b543b6a3c35'
down_revision: Union[str, Sequence[str], None] = '052f4f454bc0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('telegram_settings', sa.Column('telegram_username', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('telegram_settings', 'telegram_username')
