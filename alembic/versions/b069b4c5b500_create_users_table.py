"""Create users table

Revision ID: b069b4c5b500
Revises:
Create Date: 2025-07-10

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'b069b4c5b500'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Таблица пользователей
    op.create_table(
        'twitch_bot_users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('twitch_id', sa.String(), nullable=True),
        sa.Column('login_name', sa.String(), nullable=True),
        sa.Column('profile_image_url', sa.String(), nullable=True),
        sa.Column('access_token', sa.String(), nullable=True),  # TODO: шифровать
        sa.Column('refresh_token', sa.String(), nullable=True),  # TODO: шифровать
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('twitch_id'),
    )
    op.create_index(op.f('ix_twitch_bot_users_id'), 'twitch_bot_users', ['id'], unique=False)
    op.create_index(op.f('ix_twitch_bot_users_twitch_id'), 'twitch_bot_users', ['twitch_id'], unique=True)

    # Таблица настроек Twitch
    op.create_table(
        'twitch_user_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('twitch_bot_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('enable_help', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('enable_random', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('enable_fruit', sa.Boolean(), nullable=False, server_default='true'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_twitch_user_settings_id'), 'twitch_user_settings', ['id'], unique=False)

    # Таблица настроек Memealerts
    op.create_table(
        'memealerts_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('twitch_bot_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('memealerts_reward', UUID(as_uuid=True), nullable=True),
        sa.Column('memealerts_token', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_memealerts_settings_id'), 'memealerts_settings', ['id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_memealerts_settings_id'), table_name='memealerts_settings')
    op.drop_table('memealerts_settings')

    op.drop_index(op.f('ix_twitch_user_settings_id'), table_name='twitch_user_settings')
    op.drop_table('twitch_user_settings')

    op.drop_index(op.f('ix_twitch_bot_users_twitch_id'), table_name='twitch_bot_users')
    op.drop_index(op.f('ix_twitch_bot_users_id'), table_name='twitch_bot_users')
    op.drop_table('twitch_bot_users')
