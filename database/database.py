from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from config import settings


async_engine = create_async_engine(
    settings.db_url,
    future=True,
    echo=False,
)

AsyncSessionLocal = sessionmaker(  # type: ignore
    bind=async_engine,
    class_=AsyncSession,
    # autocommit=False,
    # autoflush=False,
    expire_on_commit=False,
)
