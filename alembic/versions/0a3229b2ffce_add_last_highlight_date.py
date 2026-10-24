"""add last_highlight_date

Revision ID: 0a3229b2ffce
Revises: 03defd472319
Create Date: 2026-10-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a3229b2ffce'
down_revision: Union[str, Sequence[str], None] = '03defd472319'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('telegram_settings', sa.Column('last_highlight_date', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('telegram_settings', 'last_highlight_date')
