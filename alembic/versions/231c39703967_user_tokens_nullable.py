"""user tokens nullable

Revision ID: 231c39703967
Revises: 0a3229b2ffce
Create Date: 2026-10-25 01:28:24.575389

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '231c39703967'
down_revision: Union[str, Sequence[str], None] = '0a3229b2ffce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('twitch_bot_users', 'access_token',
               existing_type=sa.VARCHAR(),
               nullable=True)
    op.alter_column('twitch_bot_users', 'refresh_token',
               existing_type=sa.VARCHAR(),
               nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('twitch_bot_users', 'refresh_token',
               existing_type=sa.VARCHAR(),
               nullable=False)
    op.alter_column('twitch_bot_users', 'access_token',
               existing_type=sa.VARCHAR(),
               nullable=False)
