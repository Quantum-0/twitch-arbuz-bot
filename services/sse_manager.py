import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass

import redis.asyncio as aioredis

from config import settings
from services.heat_upstream import HeatUpstreamConnection
from services.statistics import StatisticsService
from utils.enums import SSEChannel

logger = logging.getLogger(__name__)

# TTL ключа-«грейс-периода» в Redis: в течение этого окна после дисконнекта
# последнего клиента канал считается «подключённым», чтобы микро-разрывы
# EventSource (браузер ~3с реконнектится сам) не приводили к отмене наград.
SSE_GRACE_TTL_S = 15


def _grace_key(user_id: int, channel: SSEChannel) -> str:
    return f"sse:grace:{user_id}:{channel.value}"


@dataclass(eq=False)
class SSEConnection:
    queue: asyncio.Queue[str]

    def __hash__(self):
        return id(self)


class SSEManager:
    def __init__(self, statistics: StatisticsService | None = None):
        # user_id -> channel -> set[SSEConnection]
        self._connections: dict[int, dict[SSEChannel, set[SSEConnection]]] = defaultdict(lambda: defaultdict(set))
        self._heat_connections: dict[int, HeatUpstreamConnection] = {}
        self._lock = asyncio.Lock()
        self._heat_lock = asyncio.Lock()
        self._r: aioredis.Redis | None = None
        self._statistics = statistics

    async def startup(self, redis: aioredis.Redis) -> None:
        self._r = redis

    async def connect(self, user_id: int, channel: SSEChannel) -> SSEConnection:
        conn = SSEConnection(queue=asyncio.Queue())

        async with self._lock:
            self._connections[user_id][channel].add(conn)

        # Новый коннект — грейс-ключ больше не нужен (есть живой клиент).
        if self._r is not None:
            try:
                await self._r.delete(_grace_key(user_id, channel))
            except Exception:
                logger.error("Error deleting SSE grace key", exc_info=True)

        if channel == SSEChannel.HEAT:
            await self._ensure_heat(user_id)

        logger.info("SSE connected user=%s channel=%s", user_id, channel)
        return conn

    async def disconnect(self, user_id: int, channel: SSEChannel, conn: SSEConnection):
        became_empty = False
        async with self._lock:
            channels = self._connections.get(user_id)
            if not channels:
                return

            conns = channels.get(channel)
            if not conns:
                return

            conns.discard(conn)

            if not conns:
                channels.pop(channel, None)
                became_empty = True

            if not channels:
                self._connections.pop(user_id, None)

        # Последний клиент ушёл — кладём грейс-ключ, чтобы в течение SSE_GRACE_TTL_S
        # секунд канал считался «подключённым» (на случай быстрого реконнекта OBS).
        if became_empty and self._r is not None:
            try:
                await self._r.setex(_grace_key(user_id, channel), SSE_GRACE_TTL_S, "1")
            except Exception:
                logger.error("Error setting SSE grace key", exc_info=True)

        if channel == SSEChannel.HEAT:
            if not self._connections.get(user_id, {}).get(SSEChannel.HEAT):
                await self._stop_heat(user_id)

        logger.info("SSE disconnected user=%s channel=%s", user_id, channel)

    async def _ensure_heat(self, user_id: int):
        async with self._heat_lock:
            if user_id in self._heat_connections:
                return

            conn = HeatUpstreamConnection(
                user_id=user_id,
                sse_manager=self,
                url=settings.heat_url + str(user_id),
                statistics=self._statistics,
            )

            conn.start()
            self._heat_connections[user_id] = conn

    async def _stop_heat(self, user_id: int):
        conn = self._heat_connections.pop(user_id, None)
        if conn:
            await conn.stop()

    async def broadcast(self, user_id: int, channel: SSEChannel, message: str):
        async with self._lock:
            conns = list(self._connections.get(user_id, {}).get(channel, []))

        if not conns:
            return

        # payload = json.dumps(message, ensure_ascii=False)

        for conn in conns:
            # не await — чтобы один зависший клиент не тормозил всех
            conn.queue.put_nowait(message)

    async def has_clients(self, user_id: int, channel: SSEChannel | None) -> bool:
        if channel is None:
            return any(self._connections.get(user_id, {}).values())
        # Быстрый путь: есть живой in-memory коннект.
        if bool(self._connections.get(user_id, {}).get(channel)):
            return True
        # Грейс-период: последний клиент отключился недавно — считаем «ещё подключён»,
        # чтобы микро-разрывы EventSource (OBS/браузер ~3с реконнект) не роняли награды.
        if self._r is not None:
            try:
                return bool(await self._r.exists(_grace_key(user_id, channel)))
            except Exception:
                logger.error("Error checking SSE grace key", exc_info=True)
        return False

    async def snapshot(self) -> dict[str, int]:
        """Возвращает мгновенный снапшот активных SSE-подключений.

        Возвращает словарь ``{subtype: count}`` со следующими подтипами:
        - ``"total"`` — суммарное число SSE-соединений (включая дубли: один
          пользователь может держать несколько соединений на одном канале —
          например, несколько оверлеев OBS).
        - ``"unique_users"`` — число уникальных ``user_id`` с хотя бы одним
          активным соединением на любом канале.
        - ``"unique_pairs"`` — число уникальных пар ``(user_id, channel)`` —
          то есть «сколько уникальных (стример, sse-канал) комбинаций активны».
        - ``<channel.value>`` (``heat``, ``ai-sticker``, ``slovotron``, ``msg``)
          — суммарное число соединений на этом канале (включая дубли по юзерам).

        Используется APScheduler-джобом ``snapshot_sse`` (раз в минуту) для
        метрики ``StatsType.SSE_CONNECTIONS`` (gauge).
        """
        async with self._lock:
            total = 0
            unique_users: set[int] = set()
            unique_pairs = 0
            per_channel: dict[str, int] = defaultdict(int)
            for user_id, channels in self._connections.items():
                for channel, conns in channels.items():
                    n = len(conns)
                    if n == 0:
                        continue
                    total += n
                    unique_users.add(user_id)
                    unique_pairs += 1
                    per_channel[channel.value] += n
        result: dict[str, int] = {
            "total": total,
            "unique_users": len(unique_users),
            "unique_pairs": unique_pairs,
        }
        for ch_name, count in per_channel.items():
            result[ch_name] = count
        return result
