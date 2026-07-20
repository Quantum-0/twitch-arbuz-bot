import asyncio
from typing import Annotated, AsyncGenerator
from uuid import UUID, uuid3

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import StreamingResponse

from config import settings
from container import Container
from database.models import User
from dependencies import get_db
from services.sse_manager import SSEManager
from utils.enums import SSEChannel

import sqlalchemy as sa

router = APIRouter(prefix="/sse", tags=["SSE"])

# Интервал heartbeat-комментария `: ping\n\n` и таймаут чтения из очереди.
# Keep-alive держит соединение живым через прокси/роутеры и быстро детектит
# отвалившихся клиентов, а не ждёт следующего сообщения.
SSE_HEARTBEAT_S = 15


@router.get("/{user_id}/{channel}", response_class=StreamingResponse)
@inject
async def sse(
    user_id: int,
    channel: SSEChannel,
    request: Request,
    ssem: Annotated[SSEManager, Depends(Provide[Container.sse_manager])],
    db: Annotated[AsyncSession, Depends(get_db)],
    secret: UUID | None = Query(default=None)
):
    if channel == SSEChannel.SLOVOTRON:
        if secret is None:
            raise HTTPException(401, "No secret provided")
        else:
            user: User = (await db.execute(  # type: ignore
                sa.select(User).where(User.twitch_id == str(user_id))
            )).scalar_one_or_none()
            if not user:
                raise HTTPException(404, "User not found")
            if secret != uuid3(namespace=settings.slovotron_secret, name=user.login_name):
                raise HTTPException(403, "Invalid secret")
    conn = await ssem.connect(user_id, channel)

    def sse_format(data: str) -> str:
        return "".join(f"data: {line}\n" for line in data.splitlines()) + "\n"

    async def event_generator() -> AsyncGenerator[str, None]:
        # Просим браузер реконнектиться через 1с (дефолт ~3с) — меньше шансов
        # попасть в окно между дисконнектом и новым connect().
        yield "retry: 1000\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    data = await asyncio.wait_for(conn.queue.get(), timeout=SSE_HEARTBEAT_S)
                    yield sse_format(data)
                except TimeoutError:
                    # SSE-комментарий-keepalive: не создаёт события у клиента,
                    # но держит TCP-соединение живым и сбрасывает idle-таймауты прокси.
                    yield ": ping\n\n"
        finally:
            await ssem.disconnect(user_id, channel, conn)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "http://0.0.0.0:8000",
            "Access-Control-Allow-Credentials": "true",
        },
    )
