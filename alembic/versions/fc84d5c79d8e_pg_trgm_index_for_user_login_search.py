"""pg_trgm index for user login search

Расширение pg_trgm + GIN-индекс по twitch_bot_users.login_name для нечёткого
поиска пользователей (utils/user_search.py: оператор % / similarity + ILIKE).

Revision ID: fc84d5c79d8e
Revises: 231c39703967
Create Date: 2026-11-06 03:45:26.933744

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fc84d5c79d8e"
down_revision: str | Sequence[str] | None = "231c39703967"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # pg_trgm — доверенное расширение, ставится владельцем БД
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_twitch_bot_users_login_trgm",
        "twitch_bot_users",
        ["login_name"],
        postgresql_using="gin",
        postgresql_ops={"login_name": "gin_trgm_ops"},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_twitch_bot_users_login_trgm", table_name="twitch_bot_users")
