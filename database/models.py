import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    event,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from database.encrypted_string import EncryptedString
from utils.cryptography import decrypt_value, encrypt_value


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "twitch_bot_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    twitch_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    login_name: Mapped[str] = mapped_column(String, unique=True, index=True)
    profile_image_url: Mapped[str] = mapped_column(String)
    followers_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    in_beta_test: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    # role: Mapped[str] = mapped_column(
    #     String, default=None, nullable=True,
    # ) # default, beta-tester, owner, donater
    donated: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    interacted_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default="2026-01-01 00:00:00",
        nullable=False,
    )

    overlays_last_usage: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    total_deposited: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), server_default="0", nullable=False
    )

    total_spent: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), server_default="0", nullable=False
    )

    # Вычисляемый баланс
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), Computed(total_deposited - total_spent))

    _access_token: Mapped[str] = mapped_column("access_token", String)
    _refresh_token: Mapped[str] = mapped_column("refresh_token", String)

    # Связи
    settings: Mapped["TwitchUserSettings"] = relationship(
        "TwitchUserSettings",
        uselist=False,
        back_populates="user",
        cascade="all, delete",
    )
    memealerts: Mapped["MemealertsSettings"] = relationship(
        "MemealertsSettings",
        uselist=False,
        back_populates="user",
        cascade="all, delete",
    )
    links: Mapped["Links"] = relationship(
        "Links",
        uselist=False,
        back_populates="user",
        cascade="all, delete",
    )
    tts: Mapped["TTSSettings | None"] = relationship(
        "TTSSettings",
        uselist=False,
        back_populates="user",
        cascade="all, delete",
    )

    @property
    def access_token(self) -> str:
        return decrypt_value(self._access_token)  # type: ignore

    @access_token.setter
    def access_token(self, value: str) -> None:
        encrypted_value = encrypt_value(value)
        assert encrypted_value
        self._access_token = encrypted_value

    @property
    def refresh_token(self) -> str:
        return decrypt_value(self._refresh_token)  # type: ignore

    @refresh_token.setter
    def refresh_token(self, value: str) -> None:
        encrypted_value = encrypt_value(value)
        assert encrypted_value
        self._refresh_token = encrypted_value

    def __str__(self):
        return f"<User:{self.twitch_id} db object '{self.login_name}'>"


class TwitchUserSettings(Base):
    __tablename__ = "twitch_user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("twitch_bot_users.id", ondelete="CASCADE"), nullable=False)

    enable_chat_bot: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    enable_pasta: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_tg_link: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_ds_link: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_tiktok_link: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_youtube_link: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_memealerts_link: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_links_command: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    enable_bite: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_lick: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_feed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_boop: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_pat: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_hug: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_bonk: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_scratch: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    enable_banana: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_treat: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_dice: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_tail: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_whoami: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_lurk: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    enable_riot: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_pants: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    enable_horny_good: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_horny_bad: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    enable_pyramid: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_pyramid_breaker: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    enable_shoutout_on_raid: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )

    personal_pasta: Mapped[str] = mapped_column(String, default=None, nullable=True)

    memealerts_link: Mapped[str] = mapped_column(  # TODO: перенести в линкс по аналогии с другими
        String,
        default=None,
        nullable=True,
    )

    ai_sticker_reward_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, default=None)
    ai_stickers_show_in_profile: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    ai_reference_show_in_profile: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    ai_sticker_model: Mapped[str] = mapped_column(String, default="quality", server_default="quality", nullable=False)
    ai_reference_usage_policy: Mapped[str] = mapped_column(
        String, default="with_my_character", server_default="with_my_character", nullable=False
    )
    ai_reference_allow_on_other_channels: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    ai_require_own_character: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    allow_shared_chat: Mapped[bool] = mapped_column(Boolean, default=True, server_default=false(), nullable=False)
    chatbot_default_target_behaviour: Mapped[str] = mapped_column(
        String,
        default="tip",
        server_default="tip",
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", back_populates="settings")


class MemealertsSettings(Base):
    __tablename__ = "memealerts_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("twitch_bot_users.id", ondelete="CASCADE"), nullable=False)
    memealerts_reward: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, default=None)
    _memealerts_token: Mapped[str | None] = mapped_column(
        "memealerts_token", String, nullable=True
    )  # deprecated: v1 fallback
    coins_for_reward: Mapped[int] = mapped_column(
        "coins_for_reward", Integer, nullable=False, server_default="2", default=2
    )
    memecoin_name_genitive: Mapped[str | None] = mapped_column(
        "memecoin_name_genitive", String, nullable=True, default=None
    )
    memecoin_name_accusative: Mapped[str | None] = mapped_column(
        "memecoin_name_accusative", String, nullable=True, default=None
    )
    memecoin_name_genitive_multiple: Mapped[str | None] = mapped_column(
        "memecoin_name_genitive_multiple", String, nullable=True, default=None
    )
    memecoin_name_accusative_multiple: Mapped[str | None] = mapped_column(
        "memecoin_name_accusative_multiple", String, nullable=True, default=None
    )

    access_token: Mapped[str | None] = mapped_column("access_token", EncryptedString, nullable=True, default=None)
    refresh_token: Mapped[str | None] = mapped_column("refresh_token", EncryptedString, nullable=True, default=None)
    token_expires_at: Mapped[datetime | None] = mapped_column("token_expires_at", DateTime, nullable=True, default=None)
    token_refresh_expires_at: Mapped[datetime | None] = mapped_column(
        "token_refresh_expires_at", DateTime, nullable=True, default=None
    )
    token_created_at: Mapped[datetime | None] = mapped_column("token_created_at", DateTime, nullable=True, default=None)
    token_scopes: Mapped[str | None] = mapped_column("token_scopes", String, nullable=True, default=None)

    user: Mapped["User"] = relationship("User", back_populates="memealerts")

    @property
    def memealerts_token(self) -> str:
        return decrypt_value(self._memealerts_token)  # type: ignore

    @memealerts_token.setter
    def memealerts_token(self, value: str) -> None:
        self._memealerts_token = encrypt_value(value)  # type: ignore


class MemealertsSupporters(Base):
    __tablename__ = "memealerts_supporters"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    link: Mapped[str] = mapped_column(String, index=True)

    __table_args__ = (
        # Индекс на LOWER(name)
        Index("idx_memealerts_supporters_lower_name", func.lower(name)),
        # Индекс на LOWER(link) — также необходим для вашего sa.or_ запроса
        Index("idx_memealerts_supporters_lower_link", func.lower(link)),
    )


class PantsDeny(Base):
    __tablename__ = "pants_deny"

    # id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, primary_key=True)  # in lower case


class GeneratedImage(Base):
    __tablename__ = "generated_image"

    id: Mapped[uuid.UUID] = mapped_column(UUID(True), primary_key=True, default=uuid.uuid4)
    prompt: Mapped[str] = mapped_column(String, index=True, nullable=False)

    by_chatter: Mapped[str] = mapped_column(String)
    on_channel: Mapped[int] = mapped_column(Integer, index=True)

    image: Mapped[str | None] = mapped_column(String, nullable=True)  # deprecated, remove later

    file_id: Mapped[uuid.UUID] = mapped_column(UUID(True))
    shown_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=None, nullable=True)
    cost: Mapped[float] = mapped_column(
        Float,
        server_default="0",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
        index=True,
    )


class CharacterInfo(Base):
    __tablename__ = "character_info"

    id: Mapped[uuid.UUID] = mapped_column(UUID(True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=True)
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(True), nullable=True)


class RaidPasta(Base):
    __tablename__ = "twitch_pasta"

    id: Mapped[uuid.UUID] = mapped_column(UUID(True), primary_key=True, default=uuid.uuid4)
    text: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class Links(Base):
    __tablename__ = "links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("twitch_bot_users.id", ondelete="CASCADE"), nullable=False)
    telegram: Mapped[str] = mapped_column(
        String,
        default=None,
        nullable=True,
    )
    youtube: Mapped[str] = mapped_column(
        String,
        default=None,
        nullable=True,
    )
    discord: Mapped[str] = mapped_column(
        String,
        default=None,
        nullable=True,
    )
    memealerts: Mapped[str] = mapped_column(
        String,
        default=None,
        nullable=True,
    )
    tiktok: Mapped[str] = mapped_column(
        String,
        default=None,
        nullable=True,
    )

    user: Mapped["User"] = relationship("User", back_populates="links")


# Дефолтная матрица разрешений TTS: 6 ролей × 4 триггера.
# Награда не входит в матрицу — доступна всем, если создана.
# Единый каноничный источник: миграция и ORM server_default используют этот dict.
DEFAULT_TTS_PERMISSIONS: dict[str, Any] = {
    "roles": {
        "streamer": {"all": False, "all_no_replies": False, "streamer_tag": False, "command": True},
        "moderator": {"all": False, "all_no_replies": False, "streamer_tag": True, "command": True},
        "vip": {"all": False, "all_no_replies": False, "streamer_tag": False, "command": True},
        "subscriber": {"all": False, "all_no_replies": False, "streamer_tag": False, "command": True},
        "bot": {"all": False, "all_no_replies": False, "streamer_tag": False, "command": False},
        "chatter": {"all": False, "all_no_replies": False, "streamer_tag": False, "command": False},
    },
}

# JSON-литерал для server_default ORM (dollar-quoting чтобы не экранировать кавычки).
_DEFAULT_TTS_PERMS_JSON = json.dumps(DEFAULT_TTS_PERMISSIONS, ensure_ascii=False)
_DEFAULT_TTS_PERMS_SERVER_DEFAULT = text(f"$${_DEFAULT_TTS_PERMS_JSON}$$::jsonb")


class TTSSettings(Base):
    """Настройки TTS-оверлея для стримера (1:1 с User, lazy-создание).

    Строка НЕ создаётся при регистрации пользователя — только когда стример
    реально начинает настраивать TTS. До этого используются дефолты (см.
    ``utils/tts.py:get_tts_settings`` / ``ensure_tts_settings``).

    ``user_id`` — первичный ключ (отдельного суррогатного ``id`` нет, чтобы
    не плодить sequence/счётчики и держать строгую 1:1).
    """

    __tablename__ = "tts_settings"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("twitch_bot_users.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    tts_reward_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, default=None)
    read_username: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    # Матрица разрешений 6 ролей × 4 триггера.
    # Структура: {"roles": {"<role>": {"all","all_no_replies","streamer_tag","command"}}}
    permissions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=DEFAULT_TTS_PERMISSIONS, server_default=_DEFAULT_TTS_PERMS_SERVER_DEFAULT
    )
    cooldown_per_user: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    cooldown_per_channel: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_length: Mapped[int] = mapped_column(Integer, default=500, server_default="500", nullable=False)
    model: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    external_key: Mapped[str | None] = mapped_column(String, nullable=True, default=None, unique=True)

    user: Mapped["User"] = relationship("User", back_populates="tts")


class Statistics(Base):
    """10-минутные бакеты агрегированных метрик мониторинга сервиса.

    Группа ключа (``bucket_ts``, ``type``, ``subtype``, ``channel_id``) уникальна
    (см. ``statistics_pk`` с ``NULLS NOT DISTINCT`` — иначе ``channel_id=NULL``
    не схлопывался бы при ``ON CONFLICT DO UPDATE``). ``subtype`` для метрик без
    разделения хранится как пустая строка (``""``), а не NULL.
    """

    __tablename__ = "statistics"
    __table_args__ = (
        # Уникальный индекс-«первичный ключ» с NULLS NOT DISTINCT, чтобы строки
        # с channel_id=NULL участвовали в ON CONFLICT DO UPDATE (регулярный PG PK
        # считает NULL-ы различными и не отрабатывал бы конфликты). Суррогатный
        # ``id`` ниже нужен только чтобы SQLAlchemy-маппер собрался.
        Index(
            "statistics_pk",
            "bucket_ts",
            "type",
            "subtype",
            "channel_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_statistics_type_bucket", "type", "bucket_ts"),
        Index("ix_statistics_bucket_ts", "bucket_ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="Начало 10-минутного бакета (UTC, округлено вниз).",
    )
    type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="message_incoming | message_outgoing | reward_memecoins | reward_ai_stickers | command_handled",
    )
    subtype: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        server_default="",
        doc="Для reward_*: received/succeed/failed/failed_on_moderation/success; для command_handled — имя команды; иначе пустая строка.",
    )
    channel_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        default=None,
        doc="twitch_id канала (на будущее для разбивки по каналам). В MVP всегда NULL — тотал по сервису.",
    )
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sum_ms: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        default=0,
        server_default="0",
        doc=(
            "Сумма миллисекунд для timing-метрик (например, message_processing_time): "
            "count — число замеров, sum_ms — суммарное время. avg = sum_ms / count. "
            "Для count-метрик остаётся 0/NULL."
        ),
    )


@event.listens_for(User, "after_insert")
def create_settings(mapper, connection, target):
    connection.execute(TwitchUserSettings.__table__.insert().values(user_id=target.id))  # noqa
    connection.execute(MemealertsSettings.__table__.insert().values(user_id=target.id))  # noqa
    connection.execute(Links.__table__.insert().values(user_id=target.id))  # noqa


"""move_links_to_separate_table
#
# Revision ID: <авто-id>
# Revises: <id_прошлой_миграции>
# Create Date: 2026-06-03
# """
# from alembic import op
# import sqlalchemy as sa
#
# # revision identifiers, used by Alembic.
# revision = '...'
# down_revision = '...'
# branch_labels = None
# depends_on = None
#
#
# def upgrade() -> None:
#     # Шаг 1: Создаем новую таблицу links
#     op.create_table(
#         'links',
#         sa.Column('id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
#         sa.Column('tg_link', sa.String(), nullable=True),
#         sa.Column('yt_link', sa.String(), nullable=True)
#     )
#
#     # Шаг 2: ПЕРЕНОС ДАННЫХ из settings в links
#     # Используем чистый SQL для надежности и скорости в PostgreSQL
#     op.execute("""
#         INSERT INTO links (id, tg_link, yt_link)
#         SELECT id, tg_link, yt_link
#         FROM settings
#         WHERE tg_link IS NOT NULL OR yt_link IS NOT NULL;
#     """)
#
#     # Шаг 3: Удаляем старые колонки из таблицы settings
#     op.drop_column('settings', 'tg_link')
#     op.drop_column('settings', 'yt_link')
#
#
# def downgrade() -> None:
#     # Откат миграции в случае проблем
#     # 1. Возвращаем колонки в settings
#     op.add_column('settings', sa.Column('tg_link', sa.String(), nullable=True))
#     op.add_column('settings', sa.Column('yt_link', sa.String(), nullable=True))
#
#     # 2. Переносим данные обратно
#     op.execute("""
#         UPDATE settings s
#         SET tg_link = l.tg_link, yt_link = l.yt_link
#         FROM links l
#         WHERE s.id = l.id;
#     """)
#
#     # 3. Удаляем таблицу links
#     op.drop_table('links')
