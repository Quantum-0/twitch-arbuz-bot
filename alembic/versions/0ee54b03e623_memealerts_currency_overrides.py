"""memealerts currency overrides

Пользовательские склонения названия валюты на /panel/memealerts:
существующие колонки переименованы в ma_memecoin_name_* (авто-значения
Memealerts, синхронизация пишет только их), добавлены nullable
memecoin_name_*_override. Переименование через alter_column — данные
существующих склонений сохраняются.

Из автогенерата убран посторонний дрейф схемы (tts_settings.external_key,
twitch_bot_users.twitch_id_key, twitch_user_settings.ds_link/tg_link) — он не
относится к задаче.

Revision ID: 0ee54b03e623
Revises: 3f7900538feb
Create Date: 2026-11-14 03:16:48.794874

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0ee54b03e623'
down_revision: Union[str, Sequence[str], None] = '3f7900538feb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RENAMES = [
    ("memecoin_name_genitive", "ma_memecoin_name_genitive"),
    ("memecoin_name_accusative", "ma_memecoin_name_accusative"),
    ("memecoin_name_genitive_multiple", "ma_memecoin_name_genitive_multiple"),
    ("memecoin_name_accusative_multiple", "ma_memecoin_name_accusative_multiple"),
]


def upgrade() -> None:
    """Upgrade schema."""
    for old_name, new_name in _RENAMES:
        op.alter_column(
            "memealerts_settings",
            old_name,
            new_column_name=new_name,
            existing_type=sa.String(),
            existing_nullable=True,
        )
    op.add_column(
        "memealerts_settings",
        sa.Column("memecoin_name_genitive_override", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "memealerts_settings",
        sa.Column("memecoin_name_accusative_override", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "memealerts_settings",
        sa.Column("memecoin_name_genitive_multiple_override", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "memealerts_settings",
        sa.Column("memecoin_name_accusative_multiple_override", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("memealerts_settings", "memecoin_name_accusative_multiple_override")
    op.drop_column("memealerts_settings", "memecoin_name_genitive_multiple_override")
    op.drop_column("memealerts_settings", "memecoin_name_accusative_override")
    op.drop_column("memealerts_settings", "memecoin_name_genitive_override")
    for old_name, new_name in _RENAMES:
        op.alter_column(
            "memealerts_settings",
            new_name,
            new_column_name=old_name,
            existing_type=sa.String(),
            existing_nullable=True,
        )
