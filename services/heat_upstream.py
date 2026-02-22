import asyncio
import logging

from utils.enums import SSEChannel

logger = logging.getLogger(__name__)


class HeatUpstreamConnection:
    def __init__(self, user_id: int, sse_manager: "SSEManager", url: str):
        self.user_id = user_id
        self.sse_manager = sse_manager
        self.url = url

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self):
        if not self._task:
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop_event.set()
        if self._task:
            self._task.cancel()
        self._task = None

    async def _run(self):
        import websockets
        import random

        backoff = 1

        logger.info("Started new Heat upstream, id=%s", self.user_id)
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    backoff = 1

                    async for msg in ws:
                        if isinstance(msg, bytes):
                            msg = msg.decode("utf-8", errors="ignore")

                        await self.sse_manager.broadcast(
                            user_id=self.user_id,
                            channel=SSEChannel.HEAT,
                            message=msg,
                        )

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(backoff + random.uniform(0, 0.5))
                backoff = min(backoff * 2, 30)
        logger.info("Closing Heat upstream, id=%s", self.user_id)
