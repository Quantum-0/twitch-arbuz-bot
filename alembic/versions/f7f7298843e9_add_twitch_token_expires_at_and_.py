"""add twitch_token_expires_at and telegram_settings

Revision ID: f7f7298843e9
Revises: 97010cf4138d
Create Date: 2026-09-16 01:38:17.538240

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7f7298843e9"
down_revision: Union[str, Sequence[str], None] = "97010cf4138d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавляет twitch_token_expires_at на twitch_bot_users и таблицу telegram_settings."""
    # Twitch token expiry — для механизма refresh user access токенов.
    op.add_column("twitch_bot_users", sa.Column("twitch_token_expires_at", sa.DateTime(), nullable=True))

    # Telegram-интеграция: настройки 1:1 с User, lazy-создание.
    op.create_table(
        "telegram_settings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("stream_chat_id", sa.String(), nullable=True),
        sa.Column("stream_chat_type", sa.String(), nullable=True),
        sa.Column("stream_connected_at", sa.DateTime(), nullable=True),
        sa.Column("clips_chat_id", sa.String(), nullable=True),
        sa.Column("clips_chat_type", sa.String(), nullable=True),
        sa.Column("clips_connected_at", sa.DateTime(), nullable=True),
        sa.Column("stickers_chat_id", sa.String(), nullable=True),
        sa.Column("stickers_chat_type", sa.String(), nullable=True),
        sa.Column("stickers_connected_at", sa.DateTime(), nullable=True),
        sa.Column("stream_notification_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("stream_offline_behavior", sa.String(), server_default="keep", nullable=False),
        sa.Column("last_stream_message_id", sa.String(), nullable=True),
        sa.Column("clips_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("clips_mode", sa.String(), server_default="all", nullable=False),
        sa.Column("last_clip_date", sa.DateTime(), nullable=True),
        sa.Column("stickers_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("stickers_mode", sa.String(), server_default="photo", nullable=False),
        sa.Column("twitch_to_tg_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("tg_to_twitch_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("telegram_user_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["twitch_bot_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    """Откат: удаляет telegram_settings и twitch_token_expires_at."""
    op.drop_table("telegram_settings")
    op.drop_column("twitch_bot_users", "twitch_token_expires_at")
