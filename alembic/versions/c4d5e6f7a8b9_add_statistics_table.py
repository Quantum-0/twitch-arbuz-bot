"""add statistics table

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
Create Date: 2026-07-19 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "statistics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bucket_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "subtype",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    # NULLS NOT DISTINCT — чтобы строки с channel_id=NULL участвовали в ON CONFLICT.
    op.execute(
        "CREATE UNIQUE INDEX statistics_pk ON statistics "
        "(bucket_ts, type, subtype, channel_id) NULLS NOT DISTINCT"
    )
    op.create_index("ix_statistics_type_bucket", "statistics", ["type", "bucket_ts"], unique=False)
    op.create_index("ix_statistics_bucket_ts", "statistics", ["bucket_ts"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_statistics_bucket_ts", table_name="statistics")
    op.drop_index("ix_statistics_type_bucket", table_name="statistics")
    op.execute("DROP INDEX statistics_pk")
    op.drop_table("statistics")
