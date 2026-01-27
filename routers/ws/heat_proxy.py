import asyncio
import json
import logging
from json import JSONDecodeError

import websockets
from fastapi import WebSocket, APIRouter
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.websockets import WebSocketDisconnect

from config import settings

logger = logging.getLogger(__name__)



class HeatChannel:
    def __init__(self, channel_id: int):
        logger.info("Heat Channel created for channel_id=%s", channel_id)
        self.channel_id = channel_id

        # upstream
        self.ws_upstream = None
        self.reader_task: asyncio.Task | None = None

        # downstream
        self.ws_clients: set[WebSocket] = set()
        self.sse_clients: set[asyncio.Queue[str]] = set()

        self.lock = asyncio.Lock()

    # ---------- lifecycle ----------

    async def start(self):
        if self.reader_task:
            return

        logger.info("Heat upstream connect: %s", self.channel_id)

        self.ws_upstream = await websockets.connect(
            settings.heat_url + str(self.channel_id),
            ping_interval=300,
            ping_timeout=20,
        )

        self.reader_task = asyncio.create_task(self._reader())

    async def stop(self):
        logger.info("Heat upstream stop: %s", self.channel_id)

        if self.reader_task:
            self.reader_task.cancel()
            self.reader_task = None

        if self.ws_upstream:
            await self.ws_upstream.close()
            self.ws_upstream = None

    # ---------- upstream reader ----------

    async def _reader(self):
        try:
            async for msg in self.ws_upstream:
                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8", errors="ignore")

                await self.broadcast(msg)

        except Exception as e:
            logger.exception("Heat upstream error")
            await self.broadcast(json.dumps({"error": str(e)}))
        finally:
            await self.stop()

    # ---------- broadcast ----------

    async def broadcast(self, message: str):
        dead_ws = []
        dead_sse = []

        # WS clients
        for ws in self.ws_clients:
            try:
                await ws.send_text(message)
            except Exception:
                dead_ws.append(ws)

        # SSE clients
        for q in self.sse_clients:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                dead_sse.append(q)

        for ws in dead_ws:
            await self.remove_ws_client(ws)

        for q in dead_sse:
            await self.remove_sse_client(q)

    # ---------- WS clients ----------

    async def add_ws_client(self, ws: WebSocket):
        async with self.lock:
            if not self.ws_clients and not self.sse_clients:
                await self.start()

            self.ws_clients.add(ws)

    async def remove_ws_client(self, ws: WebSocket):
        async with self.lock:
            self.ws_clients.discard(ws)
            if not self.ws_clients and not self.sse_clients:
                await self.stop()

    # ---------- SSE clients ----------

    async def add_sse_client(self) -> asyncio.Queue[str]:
        q = asyncio.Queue(maxsize=10)

        async with self.lock:
            if not self.ws_clients and not self.sse_clients:
                await self.start()

            self.sse_clients.add(q)

        return q

    async def remove_sse_client(self, q: asyncio.Queue[str]):
        async with self.lock:
            self.sse_clients.discard(q)
            if not self.ws_clients and not self.sse_clients:
                await self.stop()


channels: dict[int, HeatChannel] = {}

def get_channel(channel_id: int) -> HeatChannel:
    if channel_id not in channels:
        channels[channel_id] = HeatChannel(channel_id)
    return channels[channel_id]



router_ws = APIRouter(prefix="/ws", tags=["Web Socket"])

@router_ws.websocket("/heat/{channel_id:int}")
async def heat_proxy(
    channel_id: int,
    client_ws: WebSocket,
):
    logger.info("WS channel %s connected", channel_id)
    await client_ws.accept()

    channel = get_channel(channel_id)
    await channel.add_ws_client(client_ws)

    try:
        while True:
            # если клиент шлёт данные — можно игнорировать
            received = await client_ws.receive_text()
            try:
                if json.loads(received) == {"type": "ping"}:
                    await client_ws.send_json({"type": "pong"})
            except JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        await channel.remove_ws_client(client_ws)


router_sse = APIRouter(prefix="/sse", tags=["SSE"])


def sse_format(data: str) -> str:
    return "".join(f"data: {line}\n" for line in data.splitlines()) + "\n"


@router_sse.get("/heat/{channel_id:int}")
async def heat_sse(channel_id: int, request: Request):
    channel = get_channel(channel_id)
    queue = await channel.add_sse_client()

    async def event_generator():
        try:
            yield sse_format(json.dumps({"type": "open"}))

            while True:
                if await request.is_disconnected():
                    break

                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15)
                    yield sse_format(msg)

                except asyncio.TimeoutError:
                    yield ": ping\n\n"

        finally:
            await channel.remove_sse_client(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",

            # 🔥 CORS
            "Access-Control-Allow-Origin": "http://0.0.0.0:8000",
            "Access-Control-Allow-Credentials": "true",
        },
    )


router = APIRouter()
router.include_router(router_ws)
router.include_router(router_sse)