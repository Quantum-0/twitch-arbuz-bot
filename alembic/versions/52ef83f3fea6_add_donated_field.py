"""Add donated field

Revision ID: 52ef83f3fea6
Revises: b19801ec05ae
Create Date: 2025-11-17 03:46:29.766454

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '52ef83f3fea6'
down_revision: Union[str, Sequence[str], None] = 'b19801ec05ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('twitch_bot_users', sa.Column('donated', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('twitch_bot_users', 'donated')
