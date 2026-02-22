import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass

from config import settings
from services.heat_upstream import HeatUpstreamConnection
from utils.enums import SSEChannel

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class SSEConnection:
    queue: asyncio.Queue[str]

    def __hash__(self):
        return id(self)


class SSEManager:
    def __init__(self):
        # user_id -> channel -> set[SSEConnection]
        self._connections: dict[int, dict[SSEChannel, set[SSEConnection]]] = defaultdict(
            lambda: defaultdict(set)
        )
        self._heat_connections: dict[int, HeatUpstreamConnection] = {}
        self._lock = asyncio.Lock()
        self._heat_lock = asyncio.Lock()

    async def connect(self, user_id: int, channel: SSEChannel) -> SSEConnection:
        conn = SSEConnection(queue=asyncio.Queue())

        async with self._lock:
            self._connections[user_id][channel].add(conn)

        if channel == SSEChannel.HEAT:
            await self._ensure_heat(user_id)

        logger.info("SSE connected user=%s channel=%s", user_id, channel)
        return conn

    async def disconnect(self, user_id: int, channel: SSEChannel, conn: SSEConnection):
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

            if not channels:
                self._connections.pop(user_id, None)

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

    def has_clients(self, user_id: int, channel: SSEChannel | None) -> bool:
        if channel is None:
            return any(self._connections.get(user_id, {}).values())
        return bool(self._connections.get(user_id, {}).get(channel))
