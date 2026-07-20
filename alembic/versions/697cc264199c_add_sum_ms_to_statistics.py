"""add sum_ms to statistics

Revision ID: 697cc264199c
Revises: c4d5e6f7a8b9
Create Date: 2026-07-20 03:49:08.630170

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "697cc264199c"
down_revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: добавляет колонку sum_ms для timing-метрик."""
    op.add_column(
        "statistics",
        sa.Column("sum_ms", sa.BigInteger(), server_default="0", nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("statistics", "sum_ms")
