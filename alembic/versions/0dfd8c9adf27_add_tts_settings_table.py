"""add tts settings table

Revision ID: 0dfd8c9adf27
Revises: 65f548251db7
Create Date: 2026-08-06 01:40:37.437033

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0dfd8c9adf27"
down_revision: Union[str, Sequence[str], None] = "65f548251db7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Дефолтная матрица разрешений TTS (6 ролей × 4 триггера + reward).
DEFAULT_PERMISSIONS = {
    "roles": {
        "streamer": {"all": False, "all_no_replies": False, "streamer_tag": False, "command": True},
        "moderator": {"all": False, "all_no_replies": False, "streamer_tag": True, "command": True},
        "vip": {"all": False, "all_no_replies": False, "streamer_tag": False, "command": True},
        "subscriber": {"all": False, "all_no_replies": False, "streamer_tag": False, "command": True},
        "bot": {"all": False, "all_no_replies": False, "streamer_tag": False, "command": False},
        "chatter": {"all": False, "all_no_replies": False, "streamer_tag": False, "command": True},
    },
}

import json

_DEFAULT_PERMS_JSON = json.dumps(DEFAULT_PERMISSIONS, ensure_ascii=False)
# Dollar-quoting чтобы не экранировать кавычки внутри JSON.
_PERMISSIONS_SERVER_DEFAULT = sa.text(f"$${_DEFAULT_PERMS_JSON}$$::jsonb")


def upgrade() -> None:
    op.create_table(
        "tts_settings",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("twitch_bot_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("tts_reward_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("read_username", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_PERMISSIONS_SERVER_DEFAULT,
            nullable=False,
        ),
        sa.Column("cooldown_per_user", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cooldown_per_channel", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_length", sa.Integer(), server_default="500", nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("external_key", sa.String(), nullable=True),
    )
    op.create_index("ix_tts_settings_external_key", "tts_settings", ["external_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_tts_settings_external_key", table_name="tts_settings")
    op.drop_table("tts_settings")
