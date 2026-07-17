"""Add AI sticker settings

Revision ID: 8f4a6b7c9d10
Revises: 365262eac1fa
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8f4a6b7c9d10'
down_revision: Union[str, Sequence[str], None] = '365262eac1fa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('twitch_user_settings', sa.Column('ai_stickers_show_in_profile', sa.Boolean(), server_default='true', nullable=False))
    op.add_column('twitch_user_settings', sa.Column('ai_reference_show_in_profile', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('twitch_user_settings', sa.Column('ai_sticker_model', sa.String(), server_default='quality', nullable=False))
    op.add_column('twitch_user_settings', sa.Column('ai_reference_usage_policy', sa.String(), server_default='with_my_character', nullable=False))


def downgrade() -> None:
    op.drop_column('twitch_user_settings', 'ai_reference_usage_policy')
    op.drop_column('twitch_user_settings', 'ai_sticker_model')
    op.drop_column('twitch_user_settings', 'ai_reference_show_in_profile')
    op.drop_column('twitch_user_settings', 'ai_stickers_show_in_profile')
