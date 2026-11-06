"""balance operations table

Таблица истории операций по балансу (начисления/списания) + инициализация
истории: каждому пользователю с ненулевым балансом — одна строка
initial_balance на текущий остаток (deposit при положительном, spend при
отрицательном — amount всегда положительный по CHECK). Прошлых операций в БД
нет — только итоговые total_deposited/total_spent.

Revision ID: 8ff4753d63df
Revises: fc84d5c79d8e
Create Date: 2026-11-06 04:02:28.025269

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8ff4753d63df"
down_revision: str | Sequence[str] | None = "fc84d5c79d8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "balance_operations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("twitch_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("balance_after", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(now() at time zone 'utc')"), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_balance_operations_amount_positive"),
        sa.ForeignKeyConstraint(["twitch_id"], ["twitch_bot_users.twitch_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_balance_operations_twitch_created", "balance_operations", ["twitch_id", "created_at"], unique=False)

    # Отрицательные балансы возможны (овердрафт при параллельных генерациях) —
    # для них «инициализация» записывается как списание на |balance|, иначе
    # CHECK amount > 0 уронит миграцию.
    op.execute(
        """
        INSERT INTO balance_operations (twitch_id, type, action, amount, balance_after, details, created_at)
        SELECT twitch_id,
               CASE WHEN balance > 0 THEN 'deposit' ELSE 'spend' END,
               'initial_balance',
               ABS(balance),
               balance,
               '{"note": "Баланс на момент внедрения истории операций"}'::jsonb,
               (now() at time zone 'utc')
        FROM twitch_bot_users
        WHERE balance <> 0
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_balance_operations_twitch_created", table_name="balance_operations")
    op.drop_table("balance_operations")
