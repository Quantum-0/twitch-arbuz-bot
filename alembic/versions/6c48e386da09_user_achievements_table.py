"""user_achievements table

Таблица полученных ачивок стримеров (реестр — ``utils/achievements.py``,
сервис — ``services/achievements.py``). Backfill не нужен: ленивые ачивки
вычисляются из БД при первом просмотре профиля, событийные начисляются
с момента деплоя.

Revision ID: 6c48e386da09
Revises: 0ee54b03e623
Create Date: 2026-11-15 19:36:26.304718

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6c48e386da09"
down_revision: str | Sequence[str] | None = "0ee54b03e623"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_achievements",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("twitch_id", sa.String(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("level", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("unlocked_at", sa.DateTime(), server_default=sa.text("(now() at time zone 'utc')"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["twitch_id"], ["twitch_bot_users.twitch_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_user_achievements_twitch_code", "user_achievements", ["twitch_id", "code"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_user_achievements_twitch_code", table_name="user_achievements")
    op.drop_table("user_achievements")
