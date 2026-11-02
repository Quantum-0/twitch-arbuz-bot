"""Хелперы observability: фоновые задачи без потери ошибок.

Ключевая проблема «голого» ``asyncio.create_task`` в fire-and-forget-коде:
если задача падает и её исключение никто не забирает, оно всплывает только
на GC («Task exception was never retrieved») — без контекста и с задержкой.
``spawn_logged_task`` логирует падение сразу с трейсбеком
(error → GlitchTip через дефолтную LoggingIntegration sentry-sdk).
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def spawn_logged_task(coro: Coroutine[Any, Any, T], name: str | None = None) -> asyncio.Task[T]:
    """``asyncio.create_task`` для fire-and-forget: падение задачи логируется сразу.

    Не заменяет try/except внутри самой корутины (нет ретраев и т.п.) —
    гарантирует только видимость ошибки в логах/GlitchTip.
    """
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_log_task_exception)
    return task


def _log_task_exception(fut: asyncio.Future[Any]) -> None:
    if fut.cancelled():
        return
    exc = fut.exception()
    if exc is not None:
        name = fut.get_name() if isinstance(fut, asyncio.Task) else repr(fut)
        logger.error("Background task `%s` crashed", name, exc_info=exc)


def asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:  # noqa: ARG001
    """Обработчик непойманных ошибок asyncio-loop (``loop.set_exception_handler``).

    Страховка для ``create_task``-вызовов вне ``spawn_logged_task``: логируем
    сразу с трейсбеком вместо отложенного безликого сообщения на GC.
    """
    exc = context.get("exception")
    message = context.get("message") or "unhandled exception in asyncio"
    if exc is not None:
        logger.error("Unhandled asyncio exception: %s", message, exc_info=exc)
    else:
        logger.error("Unhandled asyncio exception: %s | context=%r", message, context)


def setup_asyncio_exception_handler() -> None:
    """Установить обработчик ошибок текущего loop'а (вызывается один раз в lifespan)."""
    asyncio.get_running_loop().set_exception_handler(asyncio_exception_handler)
