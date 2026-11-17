"""Опрос серверной статистики PostgreSQL (``pg_stat_database``) для /stats.

Счётчики ``pg_stat_database`` кумулятивны (с момента старта/сброса статистики
кластера), поэтому опросщик раз в 10 минут снимает снапшот, считает дельту от
предыдущего (хранится в Redis — переживает рестарты приложения) и пишет её в
текущий 10-минутный бакет статистики через ``flush_live_delta``. В /flowviz
метрика не выводится: дельта приходит раз в 10 минут и для 2-секундного окна
скоростей бессмысленна.

Инвариант изоляции: запрос фильтрует ``WHERE datname = current_database()`` —
учитывается ТОЛЬКО база самого приложения. Соседние базы кластера (postgres,
template*, базы других сервисов) имеют собственные строки ``pg_stat_database``
и не считаются; синтетическая строка ``_shared_`` (PG 15+, общие каталоги
кластера) фильтром тоже отсекается. Фильтр убирать нельзя.
"""

import json
import logging
from collections.abc import Callable

import sqlalchemy as sa
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.api import StatsType
from services.statistics import StatisticsService

logger = logging.getLogger(__name__)

# Подтипы метрики pg_io: (колонка pg_stat_database, человеческое имя).
# Все — обычные counter'ы (блоки/строки/транзакции за интервал).
PG_IO_FIELDS: tuple[str, ...] = (
    "blks_read",
    "blks_hit",
    "tup_returned",
    "tup_fetched",
    "tup_inserted",
    "tup_updated",
    "tup_deleted",
    "xact_commit",
    "xact_rollback",
)

PG_IO_QUERY = sa.text(
    "SELECT blks_read, blks_hit, tup_returned, tup_fetched, "
    "tup_inserted, tup_updated, tup_deleted, xact_commit, xact_rollback "
    "FROM pg_stat_database WHERE datname = current_database()"
)

# Redis-ключ с предыдущим снапшотом кумулятивных счётчиков (JSON).
PREV_KEY = "pg_stats:prev"
# TTL с запасом в несколько интервалов опроса: кратковременная недоступность
# Redis не должна сбрасывать базу дельт (иначе один сэмпл просто пропускается).
PREV_TTL_SECONDS = 40 * 60


async def poll_pg_io(
    statistics: StatisticsService,
    db_session_factory: Callable[[], AsyncSession],
    redis: Redis,
) -> None:
    """Снять снапшот pg_stat_database и записать дельты в бакеты статистики.

    Первый сэмпл (нет предыдущего снапшота) только фиксирует базу для будущих
    дельт. Если счётчики уменьшились (рестарт PG / ``pg_stat_reset``) — дельты
    некорректны, пишется только новый снапшот. Новый снапшот сохраняется ДО
    записи дельт — интервал гарантированно не задваивается: сбой до сохранения
    снапшота перенесёт дельту в следующий сэмпл, сбой после сохранения (но до
    записи дельт) потеряет её один раз.
    """
    async with db_session_factory() as session:
        row = (await session.execute(PG_IO_QUERY)).mappings().first()
    if row is None:
        logger.warning("poll_pg_io: pg_stat_database has no row for current_database()")
        return
    if any(row[field] is None for field in PG_IO_FIELDS):
        # NULL бывает только при отключённом сборе статистики (track_counts=off):
        # дельты от таких значений бессмысленны — пропускаем сэмпл целиком,
        # не трогая предыдущий снапшот.
        logger.warning("poll_pg_io: NULL counters in pg_stat_database, sample skipped")
        return

    snapshot = {field: int(row[field]) for field in PG_IO_FIELDS}
    prev_raw = await redis.get(PREV_KEY)
    # Сначала фиксируем новый снапшот (см. docstring про порядок при сбоях).
    await redis.set(PREV_KEY, json.dumps(snapshot), ex=PREV_TTL_SECONDS)
    if prev_raw is None:
        return
    try:
        prev = json.loads(prev_raw)
        deltas = {field: snapshot[field] - int(prev[field]) for field in PG_IO_FIELDS}
    except (ValueError, TypeError, KeyError):
        logger.warning("poll_pg_io: corrupted previous snapshot, skipped", exc_info=True)
        return

    if any(delta < 0 for delta in deltas.values()):
        logger.info("poll_pg_io: counters decreased (PG restart / stats reset), deltas skipped")
        return
    for field, delta in deltas.items():
        if delta > 0:
            await statistics.flush_live_delta(StatsType.PG_IO, field, delta)
