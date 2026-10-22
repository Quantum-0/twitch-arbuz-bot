"""bans table

Таблица банов зрителей/стримеров (подсистема банов, см. services/bans.py).

Revision ID: 03defd472319
Revises: 65c732bb0a01
Create Date: 2026-10-20 22:02:07.075135

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "03defd472319"
down_revision: str | Sequence[str] | None = "65c732bb0a01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "bans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("target_twitch_id", sa.String(), nullable=False),
        sa.Column("target_login", sa.String(), nullable=False),
        sa.Column("target_name", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("channel_twitch_id", sa.String(), nullable=True),
        sa.Column("channel_login", sa.String(), nullable=True),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by_twitch_id", sa.String(), nullable=False),
        sa.Column("created_by_login", sa.String(), nullable=False),
        sa.Column("created_by_role", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(now() at time zone 'utc')"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "bans_target_scope_channel_key",
        "bans",
        ["target_twitch_id", "scope", "channel_twitch_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index("ix_bans_channel_twitch_id", "bans", ["channel_twitch_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_bans_channel_twitch_id", table_name="bans")
    op.drop_index("bans_target_scope_channel_key", table_name="bans", postgresql_nulls_not_distinct=True)
    op.drop_table("bans")
