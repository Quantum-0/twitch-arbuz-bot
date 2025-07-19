from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from config import settings

# SQLALCHEMY_DATABASE_URL = "postgresql://user:password@db:5432/twitch_bot"
# SQLALCHEMY_DATABASE_URL = "postgresql://user:password@localhost:45432/twitch_bot"

engine = create_engine(settings.db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
