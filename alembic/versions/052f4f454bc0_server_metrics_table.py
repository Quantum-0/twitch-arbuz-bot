"""server_metrics table

Revision ID: 052f4f454bc0
Revises: 9f36b96b79d4
Create Date: 2026-10-09 01:09:29.781611

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '052f4f454bc0'
down_revision: Union[str, Sequence[str], None] = '9f36b96b79d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'server_metrics',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bucket_ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('cpu_avg', sa.Float(), nullable=True),
        sa.Column('cpu_max', sa.Float(), nullable=True),
        sa.Column('load1_avg', sa.Float(), nullable=True),
        sa.Column('load5_avg', sa.Float(), nullable=True),
        sa.Column('load15_avg', sa.Float(), nullable=True),
        sa.Column('load5_max', sa.Float(), nullable=True),
        sa.Column('mem_used_percent', sa.Float(), nullable=True),
        sa.Column('disk_used_percent', sa.Float(), nullable=True),
        sa.Column('disk_free_gb', sa.Float(), nullable=True),
        sa.Column('net_rx_mb', sa.Float(), nullable=True),
        sa.Column('net_tx_mb', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('server_metrics_bucket_ts_key', 'server_metrics', ['bucket_ts'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('server_metrics_bucket_ts_key', table_name='server_metrics')
    op.drop_table('server_metrics')
