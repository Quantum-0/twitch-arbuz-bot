from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = "twitch_bot_users"
    id = Column(Integer, primary_key=True, index=True)
    twitch_id = Column(String, unique=True, index=True)
    login_name = Column(String)
    profile_image_url = Column(String)  # Поле для аватарки
    access_token = Column(String)
    refresh_token = Column(String)
    enable_help = Column(Boolean, default=True)
    enable_random = Column(Boolean, default=True)
    enable_fruit = Column(Boolean, default=True)