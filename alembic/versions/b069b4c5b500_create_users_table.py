"""Create users table

Revision ID: b069b4c5b500
Revises:
Create Date: 2025-07-10

"""
from alembic import op
import sqlalchemy as sa

revision = 'b069b4c5b500'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('twitch_bot_users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('twitch_id', sa.String(), nullable=True),
        sa.Column('login_name', sa.String(), nullable=True),
        sa.Column('profile_image_url', sa.String(), nullable=True),
        sa.Column('access_token', sa.String(), nullable=True),
        sa.Column('refresh_token', sa.String(), nullable=True),
        sa.Column('enable_help', sa.Boolean(), nullable=True, server_default='true'),
        sa.Column('enable_random', sa.Boolean(), nullable=True, server_default='true'),
        sa.Column('enable_fruit', sa.Boolean(), nullable=True, server_default='true'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('twitch_id')
    )
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_twitch_id'), 'users', ['twitch_id'], unique=True)

def downgrade():
    op.drop_index(op.f('ix_users_twitch_id'), table_name='users')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_table('twitch_bot_users')