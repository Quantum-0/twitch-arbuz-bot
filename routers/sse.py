from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Depends
from starlette.requests import Request
from starlette.responses import StreamingResponse

from dependencies import get_sse_manager
from services.sse_manager import SSEManager
from utils.enums import SSEChannel

router = APIRouter(prefix="/sse", tags=["SSE"])


@router.get("/{user_id}/{channel}")
async def sse(
    user_id: int,
    channel: SSEChannel,
    request: Request,
    ssem: Annotated[SSEManager, Depends(get_sse_manager)],
):
    conn = await ssem.connect(user_id, channel)

    def sse_format(data: str) -> str:
        return "".join(f"data: {line}\n" for line in data.splitlines()) + "\n"

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            while True:
                if await request.is_disconnected():
                    break

                data = await conn.queue.get()
                yield sse_format(data)
        finally:
            await ssem.disconnect(user_id, channel, conn)

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
