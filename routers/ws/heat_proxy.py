import asyncio
import json
import logging
import random
from json import JSONDecodeError
from typing import Annotated

import websockets
from fastapi import WebSocket, APIRouter, Depends
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.websockets import WebSocketDisconnect

from config import settings
from dependencies import get_ai
from services.ai import OpenAIClient

logger = logging.getLogger(__name__)



class HeatChannel:
    def __init__(self, channel_id: int):
        logger.info("Heat Channel created for channel_id=%s", channel_id)
        self.channel_id = channel_id

        # upstream
        self.ws_upstream = None
        self.runner_task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()
        self.reconnect_attempt = 0

        # downstream
        self.ws_clients: set[WebSocket] = set()
        self.sse_clients: set[asyncio.Queue[str]] = set()

        self.lock = asyncio.Lock()

    # ---------- lifecycle ----------

    async def start(self):
        if self.runner_task:
            return

        logger.info("Heat upstream supervisor start: %s", self.channel_id)

        self.stop_event.clear()
        self.runner_task = asyncio.create_task(self._run())

    async def stop(self):
        logger.info("Heat upstream supervisor stop: %s", self.channel_id)

        self.stop_event.set()

        if self.runner_task:
            self.runner_task.cancel()
            self.runner_task = None

        await self._close_upstream()

    # ---------- supervisor ----------

    async def _run(self):
        backoff_base = 1
        backoff_max = 30

        while not self.stop_event.is_set():
            try:
                await self._connect_upstream()
                self.reconnect_attempt = 0

                async for msg in self.ws_upstream:
                    if isinstance(msg, bytes):
                        msg = msg.decode("utf-8", errors="ignore")

                    await self.broadcast(msg)

            except asyncio.CancelledError:
                break

            except Exception:
                logger.exception("Heat upstream crashed")

            finally:
                await self._close_upstream()

            # если клиентов больше нет — не реконнектимся
            if not self.has_clients():
                logger.info("No clients left, stopping upstream")
                break

            self.reconnect_attempt += 1
            delay = min(backoff_base * 2 ** self.reconnect_attempt, backoff_max)
            logger.warning("Reconnect upstream in %ss", delay)

            await asyncio.sleep(delay)

    # ---------- upstream ----------

    async def _connect_upstream(self):
        logger.info("Heat upstream connect: %s", self.channel_id)

        self.ws_upstream = await websockets.connect(
            settings.heat_url + str(self.channel_id),
            ping_interval=20,
            ping_timeout=20,
        )

    async def _close_upstream(self):
        if self.ws_upstream:
            try:
                await self.ws_upstream.close()
            except Exception:
                pass
            self.ws_upstream = None

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

    def has_clients(self) -> bool:
        return bool(self.ws_clients or self.sse_clients)

    # ---------- WS clients ----------

    async def add_ws_client(self, ws: WebSocket):
        async with self.lock:
            if not self.has_clients():
                await self.start()

            self.ws_clients.add(ws)

    async def remove_ws_client(self, ws: WebSocket):
        async with self.lock:
            self.ws_clients.discard(ws)
            if not self.has_clients():
                await self.stop()

    # ---------- SSE clients ----------

    async def add_sse_client(self) -> asyncio.Queue[str]:
        q = asyncio.Queue(maxsize=10)

        async with self.lock:
            if not self.has_clients():
                await self.start()

            self.sse_clients.add(q)

        return q

    async def remove_sse_client(self, q: asyncio.Queue[str]):
        async with self.lock:
            self.sse_clients.discard(q)
            if not self.has_clients():
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


@router_sse.get("/img_gen/{channel_id:int}")
async def img_gen_sse(
    channel_id: int,
    ai: Annotated[OpenAIClient, Depends(get_ai)],
    request: Request,
):
    channel = get_channel(channel_id)
    # queue = await channel.add_sse_client()


    async def event_generator():
        try:
            # yield sse_format(json.dumps({"type": "open"}))

            while True:
                if await request.is_disconnected():
                    break

                try:
                    # msg = await asyncio.wait_for(queue.get(), timeout=15)
                    # yield sse_format(msg)
                    await asyncio.sleep(7)
                    yield sse_format(
                        await ai.get_sticker_or_cached(
                            prompt=random.choice(["banana", "яблоко", "лисёнок", "кексик"]),
                            channel=123,
                            chatter="test",
                        )
                    )
                    # yield sse_format(await generate_image(random.choice(["banana", "яблоко", "лисёнок", "кексик"])))
                    # yield sse_format("iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAAApgAAAKYB3X3/OAAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAANCSURBVEiJtZZPbBtFFMZ/M7ubXdtdb1xSFyeilBapySVU8h8OoFaooFSqiihIVIpQBKci6KEg9Q6H9kovIHoCIVQJJCKE1ENFjnAgcaSGC6rEnxBwA04Tx43t2FnvDAfjkNibxgHxnWb2e/u992bee7tCa00YFsffekFY+nUzFtjW0LrvjRXrCDIAaPLlW0nHL0SsZtVoaF98mLrx3pdhOqLtYPHChahZcYYO7KvPFxvRl5XPp1sN3adWiD1ZAqD6XYK1b/dvE5IWryTt2udLFedwc1+9kLp+vbbpoDh+6TklxBeAi9TL0taeWpdmZzQDry0AcO+jQ12RyohqqoYoo8RDwJrU+qXkjWtfi8Xxt58BdQuwQs9qC/afLwCw8tnQbqYAPsgxE1S6F3EAIXux2oQFKm0ihMsOF71dHYx+f3NND68ghCu1YIoePPQN1pGRABkJ6Bus96CutRZMydTl+TvuiRW1m3n0eDl0vRPcEysqdXn+jsQPsrHMquGeXEaY4Yk4wxWcY5V/9scqOMOVUFthatyTy8QyqwZ+kDURKoMWxNKr2EeqVKcTNOajqKoBgOE28U4tdQl5p5bwCw7BWquaZSzAPlwjlithJtp3pTImSqQRrb2Z8PHGigD4RZuNX6JYj6wj7O4TFLbCO/Mn/m8R+h6rYSUb3ekokRY6f/YukArN979jcW+V/S8g0eT/N3VN3kTqWbQ428m9/8k0P/1aIhF36PccEl6EhOcAUCrXKZXXWS3XKd2vc/TRBG9O5ELC17MmWubD2nKhUKZa26Ba2+D3P+4/MNCFwg59oWVeYhkzgN/JDR8deKBoD7Y+ljEjGZ0sosXVTvbc6RHirr2reNy1OXd6pJsQ+gqjk8VWFYmHrwBzW/n+uMPFiRwHB2I7ih8ciHFxIkd/3Omk5tCDV1t+2nNu5sxxpDFNx+huNhVT3/zMDz8usXC3ddaHBj1GHj/As08fwTS7Kt1HBTmyN29vdwAw+/wbwLVOJ3uAD1wi/dUH7Qei66PfyuRj4Ik9is+hglfbkbfR3cnZm7chlUWLdwmprtCohX4HUtlOcQjLYCu+fzGJH2QRKvP3UNz8bWk1qMxjGTOMThZ3kvgLI5AzFfo379UAAAAASUVORK5CYII=")

                except asyncio.TimeoutError:
                    pass
                    # yield ": ping\n\n"

        finally:
            pass
            # await channel.remove_sse_client(queue)

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

# TODO: REFACTOR ALL THIS SHIT
