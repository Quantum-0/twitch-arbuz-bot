"""memealerts deposits table

История покупок мемкоинов через награду за баллы канала (/panel/memealerts).
UNIQUE-индекс на redemption_id — дедупликация повторной доставки вебхука EventSub.
Backfill не нужен: до этой таблицы история нигде не хранилась.

Из автогенерата убран посторонний дрейф схемы (tts_settings.external_key,
twitch_bot_users.twitch_id_key, twitch_user_settings.ds_link/tg_link) — он не
относится к задаче.

Revision ID: 3f7900538feb
Revises: 8ff4753d63df
Create Date: 2026-11-13 23:27:42.863788

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '3f7900538feb'
down_revision: Union[str, Sequence[str], None] = '8ff4753d63df'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'memealerts_deposits',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('twitch_id', sa.String(), nullable=False),
        sa.Column('redemption_id', sa.UUID(), nullable=False),
        sa.Column('chatter_twitch_id', sa.String(), nullable=False),
        sa.Column('chatter_login', sa.String(length=25), nullable=False),
        sa.Column('memealerts_username', sa.String(length=200), nullable=False),
        sa.Column('points_spent', sa.Integer(), nullable=True),
        sa.Column('memecoins', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('error', sa.String(length=32), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            server_default=sa.text("(now() at time zone 'utc')"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['twitch_id'], ['twitch_bot_users.twitch_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_memealerts_deposits_twitch_id_id', 'memealerts_deposits', ['twitch_id', 'id'], unique=False)
    op.create_index('memealerts_deposits_redemption_id_key', 'memealerts_deposits', ['redemption_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('memealerts_deposits_redemption_id_key', table_name='memealerts_deposits')
    op.drop_index('ix_memealerts_deposits_twitch_id_id', table_name='memealerts_deposits')
    op.drop_table('memealerts_deposits')
