import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, ForeignKey, Integer, event, false, func, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, DeclarativeBase, Mapped, mapped_column

from utils.cryptography import decrypt_value, encrypt_value


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "twitch_bot_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    twitch_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    login_name: Mapped[str] = mapped_column(String)
    profile_image_url: Mapped[str] = mapped_column(String)
    followers_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    in_beta_test: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    _access_token: Mapped[str] = mapped_column("access_token", String)
    _refresh_token: Mapped[str] = mapped_column("refresh_token", String)

    # Связи
    settings: Mapped["TwitchUserSettings"] = relationship(
        "TwitchUserSettings", uselist=False, back_populates="user", cascade="all, delete"
    )
    memealerts: Mapped["MemealertsSettings"] = relationship(
        "MemealertsSettings", uselist=False, back_populates="user", cascade="all, delete"
    )

    @property
    def access_token(self) -> str:
        return decrypt_value(self._access_token)

    @access_token.setter
    def access_token(self, value: str) -> None:
        self._access_token = encrypt_value(value)

    @property
    def refresh_token(self) -> str:
        return decrypt_value(self._refresh_token)

    @refresh_token.setter
    def refresh_token(self, value: str) -> None:
        self._refresh_token = encrypt_value(value)


class TwitchUserSettings(Base):
    __tablename__ = "twitch_user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("twitch_bot_users.id", ondelete="CASCADE"), nullable=False)

    enable_chat_bot: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    enable_bite: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_lick: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_boop: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_pat: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    enable_banana: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_whoami: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_lurk: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    enable_riot: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_pants: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)

    enable_pyramid: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    enable_pyramid_breaker: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)


    user: Mapped["User"] = relationship("User", back_populates="settings")


class MemealertsSettings(Base):
    __tablename__ = "memealerts_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("twitch_bot_users.id", ondelete="CASCADE"), nullable=False)
    memealerts_reward: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, default=None)
    _memealerts_token: Mapped[str | None] = mapped_column("memealerts_token", String, nullable=True)
    coins_for_reward: Mapped[int] = mapped_column("coins_for_reward", Integer, nullable=False, server_default="2", default=2)

    user: Mapped["User"] = relationship("User", back_populates="memealerts")

    @property
    def memealerts_token(self) -> str:
        return decrypt_value(self._memealerts_token)

    @memealerts_token.setter
    def memealerts_token(self, value: str) -> None:
        self._memealerts_token = encrypt_value(value)


class MemealertsSupporters(Base):
    __tablename__ = "memealerts_supporters"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    link: Mapped[str] = mapped_column(String, index=True)


@event.listens_for(User, "after_insert")
def create_settings(mapper, connection, target):
    connection.execute(
        TwitchUserSettings.__table__.insert().values(user_id=target.id)
    )
    connection.execute(
        MemealertsSettings.__table__.insert().values(user_id=target.id)
    )
