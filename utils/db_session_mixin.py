from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession

class DBSessionMixin:
    def __init__(self, *, db_session_factory: Callable[[], AsyncSession] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._db_session_factory = db_session_factory

    def _get_db_session_factory(self) -> Callable[[], AsyncSession]:
        if self._db_session_factory is None:
            raise RuntimeError("DB session factory was not configured for this component.")
        return self._db_session_factory

    @asynccontextmanager
    async def db_session(self) -> AsyncIterator[AsyncSession]:
        async with self._get_db_session_factory()() as session:
            yield session

# TODO: вставить это в command и в handler