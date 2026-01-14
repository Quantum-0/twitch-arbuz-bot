import asyncio
import json
import logging

import websockets
from fastapi import APIRouter
from fastapi import WebSocket, WebSocketDisconnect

from config import settings

logger = logging.getLogger(__name__)



class HeatChannel:
    def __init__(self, channel_id: int):
        self.channel_id = channel_id
        self.ws_upstream = None
        self.clients: set[WebSocket] = set()
        self.reader_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    async def start(self):
        if self.reader_task:
            return

        self.ws_upstream = await websockets.connect(
            settings.heat_url + str(self.channel_id)
        )
        self.reader_task = asyncio.create_task(self._reader())

    async def stop(self):
        if self.reader_task:
            self.reader_task.cancel()
            self.reader_task = None

        if self.ws_upstream:
            await self.ws_upstream.close()
            self.ws_upstream = None

    async def _reader(self):
        try:
            async for msg in self.ws_upstream:
                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8", errors="ignore")

                await self.broadcast(msg)

        except Exception as e:
            await self.broadcast(json.dumps({"error": str(e)}))
        finally:
            await self.stop()

    async def broadcast(self, message: str):
        dead = []

        for ws in self.clients:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            await self.remove_client(ws)

    async def add_client(self, ws: WebSocket):
        async with self.lock:
            if not self.clients:
                await self.start()

            self.clients.add(ws)

    async def remove_client(self, ws: WebSocket):
        async with self.lock:
            self.clients.discard(ws)
            if not self.clients:
                await self.stop()


channels: dict[int, HeatChannel] = {}

def get_channel(channel_id: int) -> HeatChannel:
    if channel_id not in channels:
        channels[channel_id] = HeatChannel(channel_id)
    return channels[channel_id]



router = APIRouter(prefix="/ws", tags=["Web Socket"])

@router.websocket("/heat/{channel_id:int}")
async def heat_proxy(
    channel_id: int,
    client_ws: WebSocket,
):
    logger.info("WS channel %s connected", channel_id)
    await client_ws.accept()

    channel = get_channel(channel_id)
    await channel.add_client(client_ws)

    try:
        while True:
            # если клиент шлёт данные — можно игнорировать
            await client_ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await channel.remove_client(client_ws)


    # await client_ws.accept()
    #
    # try:
    #     async with websockets.connect(settings.heat_url + str(channel_id)) as external_ws:
    #
    #         async def client_to_external():
    #             try:
    #                 while True:
    #                     msg = await client_ws.receive()
    #                     if msg["type"] == "websocket.receive":
    #                         if "text" in msg:
    #                             await external_ws.send(msg["text"])
    #                         elif "bytes" in msg:
    #                             await external_ws.send(msg["bytes"])
    #             except WebSocketDisconnect:
    #                 pass
    #
    #         async def external_to_client():
    #             try:
    #                 async for msg in external_ws:
    #                     if isinstance(msg, bytes):
    #                         await client_ws.send_bytes(msg)
    #                     else:
    #                         await client_ws.send_text(msg)
    #             except Exception:
    #                 pass
    #
    #         await asyncio.gather(
    #             client_to_external(),
    #             external_to_client(),
    #         )
    #
    # finally:
    #     await client_ws.close()
