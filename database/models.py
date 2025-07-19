import uuid
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = "twitch_bot_users"

    id = Column(Integer, primary_key=True, index=True)
    twitch_id = Column(String, unique=True, index=True)
    login_name = Column(String)
    profile_image_url = Column(String)
    access_token = Column(String)
    refresh_token = Column(String)

    # Связи
    settings: "TwitchUserSettings" = relationship("TwitchUserSettings", uselist=False, back_populates="user", cascade="all, delete")
    memealerts: "MemealertsSettings" = relationship("MemealertsSettings", uselist=False, back_populates="user", cascade="all, delete")


class TwitchUserSettings(Base):
    __tablename__ = "twitch_user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("twitch_bot_users.id", ondelete="CASCADE"), nullable=False)
    enable_help = Column(Boolean, default=True, nullable=False)
    enable_random = Column(Boolean, default=True, nullable=False)
    enable_fruit = Column(Boolean, default=True, nullable=False)

    user = relationship("User", back_populates="settings")


class MemealertsSettings(Base):
    __tablename__ = "memealerts_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("twitch_bot_users.id", ondelete="CASCADE"), nullable=False)
    memealerts_reward = Column(UUID(as_uuid=True), nullable=True, default=None)
    memealerts_token = Column(String, nullable=True)

    user = relationship("User", back_populates="memealerts")


@event.listens_for(User, "after_insert")
def create_settings(mapper, connection, target):
    connection.execute(
        TwitchUserSettings.__table__.insert().values(
            user_id=target.id,
        )
    )
    connection.execute(
        MemealertsSettings.__table__.insert().values(
            user_id=target.id,
        )
    )
