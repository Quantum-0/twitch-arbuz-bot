from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from config import settings

engine_kwargs = {
    "future": True,
    "echo": False,
}

if not str(settings.db_url).startswith("sqlite"):
    engine_kwargs.update({
        "pool_size": 5,
        "max_overflow": 10,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_timeout": 5,
    })

async_engine = create_async_engine(
    settings.db_url,
    **engine_kwargs
)

SQLAlchemyInstrumentor().instrument(engine=async_engine.sync_engine)

AsyncSessionLocal = sessionmaker(  # type: ignore
    bind=async_engine,
    class_=AsyncSession,
    # autocommit=False,
    # autoflush=False,
    expire_on_commit=False,
)
