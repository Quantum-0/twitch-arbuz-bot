from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from config import settings

async_engine = create_async_engine(
    settings.db_url,
    future=True,
    echo=False,

    # --- НАСТРОЙКИ ПУЛА СОЕДИНЕНИЙ ---
    pool_size=5,  # Базовое количество постоянно открытых соединений
    max_overflow=10,  # Сколько соединений можно открыть сверх pool_size при пиках нагрузки

    # --- ЗАЩИТА ОТ ОБРЫВОВ И ТАЙМАУТОВ ---
    pool_pre_ping=True,  # Проверяет коннект на "живость" перед каждым запросом (решает InterfaceError)
    pool_recycle=1800,  # Автоматически пересоздает соединения старше 30 минут. Дефолт = -1
    pool_timeout=5,  # Сколько секунд роут будет ждать свободный коннект из пула, прежде чем выбросить ошибку. Дефолт = 30

)

SQLAlchemyInstrumentor().instrument(engine=async_engine.sync_engine)

AsyncSessionLocal = sessionmaker(  # type: ignore
    bind=async_engine,
    class_=AsyncSession,
    # autocommit=False,
    # autoflush=False,
    expire_on_commit=False,
)
