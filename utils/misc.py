import asyncio
import trace
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


async def call_with_delay(timer: float, func: Awaitable[T]) -> T:
    await asyncio.sleep(timer)
    return await func


from opentelemetry.context import attach, detach, Context


async def run_in_clean_otel_context(coro):
    """Сбрасывает текущий контекст OpenTelemetry и запускает корутину в новом трейсе"""
    token = attach(Context()) # Полный разрыв связи
    try:
        await coro
    finally:
        detach(token)