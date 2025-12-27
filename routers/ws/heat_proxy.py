import asyncio
import logging

import websockets
from fastapi import APIRouter
from fastapi import WebSocket, WebSocketDisconnect

from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["Web Socket"])

@router.websocket("/heat/{channel_id:int}")
async def heat_proxy(
    channel_id: int,
    client_ws: WebSocket,
):
    logger.info("WS channel %s connected", channel_id)
    await client_ws.accept()

    try:
        async with websockets.connect(settings.heat_url + str(channel_id)) as external_ws:

            async def client_to_external():
                try:
                    while True:
                        msg = await client_ws.receive()
                        if msg["type"] == "websocket.receive":
                            if "text" in msg:
                                await external_ws.send(msg["text"])
                            elif "bytes" in msg:
                                await external_ws.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass

            async def external_to_client():
                try:
                    async for msg in external_ws:
                        if isinstance(msg, bytes):
                            await client_ws.send_bytes(msg)
                        else:
                            await client_ws.send_text(msg)
                except Exception:
                    pass

            await asyncio.gather(
                client_to_external(),
                external_to_client(),
            )

    finally:
        await client_ws.close()
