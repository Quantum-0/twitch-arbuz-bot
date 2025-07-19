import uuid
from sqlalchemy import String, Boolean, ForeignKey, Integer, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "twitch_bot_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    twitch_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    login_name: Mapped[str] = mapped_column(String)
    profile_image_url: Mapped[str] = mapped_column(String)
    access_token: Mapped[str] = mapped_column(String)
    refresh_token: Mapped[str] = mapped_column(String)

    # Связи
    settings: Mapped["TwitchUserSettings"] = relationship(
        "TwitchUserSettings", uselist=False, back_populates="user", cascade="all, delete"
    )
    memealerts: Mapped["MemealertsSettings"] = relationship(
        "MemealertsSettings", uselist=False, back_populates="user", cascade="all, delete"
    )


class TwitchUserSettings(Base):
    __tablename__ = "twitch_user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("twitch_bot_users.id", ondelete="CASCADE"), nullable=False)
    enable_help: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enable_random: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enable_fruit: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="settings")


class MemealertsSettings(Base):
    __tablename__ = "memealerts_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("twitch_bot_users.id", ondelete="CASCADE"), nullable=False)
    memealerts_reward: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, default=None)
    memealerts_token: Mapped[str | None] = mapped_column(String, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="memealerts")


@event.listens_for(User, "after_insert")
def create_settings(mapper, connection, target):
    connection.execute(
        TwitchUserSettings.__table__.insert().values(user_id=target.id)
    )
    connection.execute(
        MemealertsSettings.__table__.insert().values(user_id=target.id)
    )
