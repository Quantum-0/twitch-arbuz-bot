"""WebSocket-эндпоинт для подключения вычислительных нод TTS+RVC.

Нода подключается к ``GET /nodes/ws?token=<node_auth_token>`` и обменивается
JSON-сообщениями (см. ``schemas/node.py``). Авторизация — фиксированный
ключ из ``settings.node_auth_token`` (без per-operator токенов).

Поток:
1. Нода → ``node:register`` (список моделей, capabilities) → бот ack'ает
   ``node:registered``.
2. Нода → ``node:ping`` каждые 10с → бот → ``node:pong``.
3. Бот → ``tts:task`` → нода → ``tts:result``/``tts:error``.
4. Бот → ``tts:cancel`` (опц., при таймауте/падении ноды).

При разрыве WS нода удаляется из реестра ``NodeManager`` (задачи ретраятся).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.params import Depends

from config import settings
from container import Container
from schemas.node import (
    NodePingMessage,
    NodeRegisterMessage,
    TTSErrorMessage,
    TTSResultMessage,
)
from services.node_manager import NodeManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nodes", tags=["Nodes"])

# Код закрытия WS при неверном токене.
_CLOSE_AUTH_FAIL = 4401


@router.websocket("/ws")
@inject
async def nodes_ws(
    ws: WebSocket,
    node_manager: Annotated[NodeManager, Depends(Provide[Container.node_manager])],
    token: str = "",
):
    # 1. Авторизация фиксированным ключом (до accept, чтобы не плодить соединения).
    expected = settings.node_auth_token.get_secret_value()
    if not expected or not hmac.compare_digest(token, expected):
        await ws.close(code=_CLOSE_AUTH_FAIL, reason="Invalid node token")
        return

    await ws.accept()
    logger.info("Nodes WS connected from %s", ws.client)

    try:
        await _message_loop(ws, node_manager)
    except WebSocketDisconnect:
        logger.info("Nodes WS disconnected")
    except Exception:
        logger.exception("Nodes WS error")
    finally:
        await node_manager.drop_by_ws(ws)


async def _message_loop(ws: WebSocket, nm: NodeManager) -> None:
    while True:
        raw = await ws.receive_text()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Nodes WS: invalid JSON, skipping")
            continue

        mtype = msg.get("type")
        if mtype == "node:register":
            await _on_register(ws, nm, msg)
        elif mtype == "node:ping":
            _on_ping(ws, nm, msg)
        elif mtype == "tts:result":
            await _on_result(nm, msg)
        elif mtype == "tts:error":
            await _on_error(nm, msg)
        else:
            logger.debug("Nodes WS: unknown message type %r", mtype)


async def _on_register(ws: WebSocket, nm: NodeManager, msg: dict) -> None:
    parsed = NodeRegisterMessage.model_validate(msg)
    await nm.register(
        ws=ws,
        node_id=parsed.node_id,
        models=[m.id for m in parsed.models],
        capabilities=parsed.capabilities.model_dump(),
    )
    await ws.send_text(
        json.dumps(
            {
                "type": "node:registered",
                "node_id": parsed.node_id,
                "heartbeat_interval_s": 10,
                "heartbeat_timeout_s": 20,
            }
        )
    )


def _on_ping(ws: WebSocket, nm: NodeManager, msg: dict) -> None:
    parsed = NodePingMessage.model_validate(msg)
    nm.heartbeat(parsed.node_id, parsed.load)
    asyncio.create_task(_send_pong(ws))


async def _send_pong(ws: WebSocket) -> None:
    await ws.send_text(json.dumps({"type": "node:pong", "ts": int(time.time())}))


async def _on_result(nm: NodeManager, msg: dict) -> None:
    parsed = TTSResultMessage.model_validate(msg)
    await nm.handle_result(parsed.task_id, parsed.audio_b64, parsed.format)


async def _on_error(nm: NodeManager, msg: dict) -> None:
    parsed = TTSErrorMessage.model_validate(msg)
    await nm.handle_error(parsed.task_id, parsed.code, parsed.message)
