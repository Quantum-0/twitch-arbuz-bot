"""Live-счётчики процесса для живой карты потоков ``/flowviz``.

Процесс приложения строго однопроцессный (uvicorn без ``--workers``: SSEManager,
чат-бот и MQTT-клиент живут в памяти процесса), поэтому кумулятивные счётчики
в памяти корректно отражают 100 % трафика без блокировок — все инкременты
выполняются в одном event loop.

Как это устроено:

* ``LiveRegistry`` — накопитель кумулятивных счётчиков ``{(type, subtype): total}``.
  ``bump`` синхронный и дешёвый (инкремент dict) — его можно звать из горячих
  путей и sync-колбэков (ивенты SQLAlchemy, ``httpx.send``) без
  ``asyncio.create_task`` на каждую операцию.
* ``StatisticsService.inc`` дополнительно дублирует свой инкремент в реестр
  (``bump_persisted``) — все «обычные» метрики (сообщения чата, награды,
  Heat-прокси) сразу видны live без правок точек инкремента.
* Высокочастотные новые метрики (SQL-запросы, команды Redis, SSE-broadcast,
  вызовы Twitch API) пишутся ТОЛЬКО в реестр (``bump``); джоба
  ``flush_live_metrics`` (раз в 10 с, dependencies.py) переносит накопленные
  дельты в обычную статистику — Redis-бакеты → таблица ``statistics`` →
  графики ``/stats``.
* ``rates()`` считает скорости диффом кумулятивных сэмплов (лениво сэмплит при
  каждом вызове, окно ~2 с) — этого достаточно поллингу ``/flowviz`` раз в
  секунду.

Реестр — модульный синглтон (по аналогии с ``tracer``): к нему нужен доступ из
мест без DI-контейнера и без гарантированного running loop. Если приложение
когда-нибудь станет многопроцессным (``--workers``), live-числа станут
per-worker — ограничение зафиксировано в AGENTS.md.
"""

import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from schemas.api import StatsType

logger = logging.getLogger(__name__)

_Key = tuple[str, str]


class LiveRegistry:
    """Кумулятивные live-счётчики процесса + сэмплы для расчёта скоростей.

    Потокобезопасность не нужна: однопроцессное приложение с одним event loop,
    все методы синхронные и не ожидают ничего.
    """

    MAX_SAMPLES = 15
    MIN_SAMPLE_INTERVAL = 0.8

    def __init__(self) -> None:
        # Кумулятивные итоги (монотонно растут, никогда не сбрасываются) —
        # по ним считается rates().
        self._totals: dict[_Key, int] = {}
        # Дельты, ожидающие переноса в StatisticsService (только live-only
        # счётчики, пишущиеся через bump()).
        self._pending: dict[_Key, int] = {}
        self._samples: deque[tuple[float, dict[_Key, int]]] = deque(maxlen=self.MAX_SAMPLES)

    # ------------------------------------------------------------------
    # Инкремент
    # ------------------------------------------------------------------

    def bump(self, type_: str | StatsType, subtype: str = "", n: int = 1) -> None:
        """Live-only инкремент: в Redis попадёт через ``flush_live_metrics``."""
        key = (str(type_), subtype)
        self._totals[key] = self._totals.get(key, 0) + n
        self._pending[key] = self._pending.get(key, 0) + n

    def bump_persisted(self, type_: str | StatsType, subtype: str = "", n: int = 1) -> None:
        """Инкремент для счётчиков, уже пишущихся в Redis самим ``inc()``.

        В ``_pending`` не попадает — иначе flusher посчитал бы их дважды.
        """
        key = (str(type_), subtype)
        self._totals[key] = self._totals.get(key, 0) + n

    def take_pending(self) -> dict[_Key, int]:
        """Атомарно забрать накопленные дельты для flusher'а (и обнулить)."""
        out = {key: n for key, n in self._pending.items() if n > 0}
        self._pending.clear()
        return out

    def reset(self) -> None:
        """Полный сброс (только для тестов)."""
        self._totals.clear()
        self._pending.clear()
        self._samples.clear()

    # ------------------------------------------------------------------
    # Скорости
    # ------------------------------------------------------------------

    def sample(self, now: float | None = None) -> None:
        """Снапшот кумулятивных итогов в кольцевой буфер (не чаще раза в интервал)."""
        ts = time.monotonic() if now is None else now
        if self._samples and ts - self._samples[-1][0] < self.MIN_SAMPLE_INTERVAL:
            return
        self._samples.append((ts, dict(self._totals)))

    def rates(self, window: float = 2.0, now: float | None = None) -> dict[_Key, float]:
        """Скорости (единиц/сек) по диффу сэмплов внутри ``window`` секунд.

        Лениво добавляет свежий сэмпл, поэтому корректна при вызове с любой
        частотой (поллинг страницы раз в 1 с). При ``len(samples) < 2``
        возвращает пустой словарь.
        """
        self.sample(now=now)
        if len(self._samples) < 2:
            return {}
        last_ts, last_vals = self._samples[-1]
        # Опорный сэмпл — первый, попавший в окно (или самый старый вообще).
        ref_ts, ref_vals = self._samples[0]
        for ts, vals in self._samples:
            if ts >= last_ts - window:
                ref_ts, ref_vals = ts, vals
                break
        dt = last_ts - ref_ts
        if dt <= 0:
            return {}
        result: dict[_Key, float] = {}
        for key, total in last_vals.items():
            delta = total - ref_vals.get(key, 0)
            if delta > 0:
                result[key] = delta / dt
        return result


_registry = LiveRegistry()


def live_metrics() -> LiveRegistry:
    """Доступ к процессному синглтону реестра (без DI — см. модульный docstring)."""
    return _registry


# ----------------------------------------------------------------------
# Перенос live-счётчиков в обычную статистику (джоба раз в 10 с)
# ----------------------------------------------------------------------


async def flush_live_metrics() -> None:
    """Перенести накопленные live-дельты в Redis-бакеты StatisticsService.

    Вызывается APScheduler-джобой ``flush_live_metrics`` (dependencies.py).
    Ошибка переноса одной метрики не прерывает остальные; потерянная дельта
    влияет только на историю в /stats (live-числа в ``_totals`` остаются).
    """
    from container_runtime import get_container

    statistics = get_container().statistics()
    for (type_, subtype), amount in live_metrics().take_pending().items():
        if amount <= 0:
            continue
        try:
            await statistics.flush_live_delta(type_, subtype, amount)
        except Exception:
            logger.error("flush_live_metrics failed for %s/%s", type_, subtype, exc_info=True)


# ----------------------------------------------------------------------
# Инструментация: PostgreSQL (engine-level events)
# ----------------------------------------------------------------------


def install_db_query_counter(sync_engine: Any) -> None:
    """Повесить счётчик SQL-запросов на единственный engine приложения.

    ``before_cursor_execute`` ловит 100 % SQL приложения (все сессии идут через
    один ``async_engine``). Колбэк синхронный — работает в greenlet-контексте
    SQLAlchemy async, поэтому инкрементит только live-реестр (никаких
    ``create_task``). SQL jobstore'а APScheduler ходит через собственный
    sync-engine и сюда не попадает (осознанно — служебный шум).
    """
    from sqlalchemy import event

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _count_db_query(
        conn: Any,  # noqa: ARG001
        cursor: Any,  # noqa: ARG001
        statement: str,
        parameters: Any,  # noqa: ARG001
        context: Any,  # noqa: ARG001
        executemany: bool,  # noqa: ARG001
    ) -> None:
        verb = statement.strip().split(None, 1)[0].upper()[:24] if statement else "OTHER"
        _registry.bump(StatsType.DB_QUERIES, verb)


# ----------------------------------------------------------------------
# Инструментация: Twitch API через raw-httpx (twitch.py, clips_poller, token_service)
# ----------------------------------------------------------------------

# Вызовы twitchAPI-клиента идут через aiohttp и считаются обёрткой в
# ``Twitch.startup()``; здесь — перехват наших прямых httpx-вызовов к хостам
# Twitch. Двойного счёта нет: каналы непересекающиеся.
_TWITCH_HOST_SUBTYPE: dict[str, str] = {
    "api.twitch.tv": "helix",
    "id.twitch.tv": "oauth",
}


def _bump_twitch_httpx(host: str | None) -> None:
    subtype = _TWITCH_HOST_SUBTYPE.get(host or "")
    if subtype is not None:
        _registry.bump(StatsType.TWITCH_API_CALLS, subtype)


def install_httpx_twitch_counter() -> None:
    """Обернуть ``httpx.AsyncClient.send`` счётчиком вызовов к хостам Twitch.

    Патчит класс глобально (одна точка на все ~15 прямых ``httpx``-вызовов в
    ``twitch/client/twitch.py``, ``services/clips_poller.py``,
    ``services/twitch_token_service.py``). Должен вызываться ПОСЛЕ
    ``HTTPXClientInstrumentor().instrument()`` — тогда оборачивает уже
    инструментированный send. Идемпотентен.
    """
    import httpx

    if getattr(httpx.AsyncClient.send, "_twibot_live_counter", False):
        return

    orig_send: Callable[..., Any] = httpx.AsyncClient.send

    async def _counting_send(self: httpx.AsyncClient, request: httpx.Request, *args: Any, **kwargs: Any) -> Any:
        _bump_twitch_httpx(request.url.host)
        return await orig_send(self, request, *args, **kwargs)

    _counting_send._twibot_live_counter = True  # type: ignore[attr-defined]
    httpx.AsyncClient.send = _counting_send  # type: ignore[method-assign]
