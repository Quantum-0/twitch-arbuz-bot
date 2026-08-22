"""NodeManager — оркестратор пула вычислительных нод TTS+RVC.

Хранит реестр подключённых по WebSocket нод, индекс доступных моделей
(``model_id → [node_id]``), диспетчер задач (``task_id → Future``) и
фоновый sweeper, удаляющий ноды, переставшие слать heartbeat
(по умолчанию 20с).

Маршрутизация задачи (``dispatch_task``): выбирает наименее загруженную
живую ноду с нужной моделью (``capabilities.gpu == True``), шлёт ``tts:task``
через WS и ждёт ``tts:result``/``tts:error`` в течение ``task_timeout_s``.
При таймауте/отключении ноды — одна попытка переназначения на другую ноду.

Метрики (через ``StatisticsService``, fire-and-forget):
- ``NODES_ACTIVE`` (gauge): snapshot активных нод (total/gpu).
- ``NODE_TASKS`` (counter): dispatched/succeeded/failed/timeout/requeued.
- ``NODE_TASK_TIME`` (timing): dispatch → result.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemas.api import StatsType
from services.statistics import StatisticsService

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)


class NodeUnavailableError(Exception):
    """Нет онлайн-ноды с запрошенной моделью."""


class NodeTimeoutError(Exception):
    """Задача на ноде превысила task_timeout_s (после всех ретраев)."""


class NodeTaskError(Exception):
    """Нода вернула tts:error."""


@dataclass(eq=False)
class NodeConn:
    """Активное WS-соединение ноды."""

    ws: WebSocket
    node_id: str
    models: list[str] = field(default_factory=list)
    capabilities: dict = field(default_factory=dict)
    last_seen: float = 0.0
    inflight: int = 0
    max_concurrent: int = 1
    gpu: bool = False

    @property
    def alive(self) -> bool:
        return self.last_seen > time.monotonic() - 20


@dataclass
class _PendingTask:
    """Параметры отправленной задачи — для ретрая при падении ноды."""

    future: asyncio.Future[tuple[str, str]]
    text: str
    model: str
    voice: str
    pitch: int
    fmt: str
    speed: float
    target_node_id: str
    started_monotonic: float
    attempts: int = 0


class NodeManager:
    """Реестр нод + диспетчер TTS-задач."""

    def __init__(
        self,
        statistics: StatisticsService | None = None,
        heartbeat_timeout_s: int = 20,
        task_timeout_s: int = 30,
        max_attempts: int = 2,
    ) -> None:
        self._nodes: dict[str, NodeConn] = {}
        self._by_model: dict[str, list[str]] = {}
        self._pending: dict[str, _PendingTask] = {}
        self._lock = asyncio.Lock()
        self._statistics = statistics
        self._hb_timeout = heartbeat_timeout_s
        self._task_timeout = task_timeout_s
        self._max_attempts = max_attempts
        self._sweeper: asyncio.Task[None] | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def startup(self) -> None:
        self._sweeper = asyncio.create_task(self._heartbeat_sweeper())

    async def shutdown(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            try:
                await self._sweeper
            except asyncio.CancelledError:
                pass
            self._sweeper = None
        async with self._lock:
            self._nodes.clear()
            self._by_model.clear()
            for task in self._pending.values():
                if not task.future.done():
                    task.future.set_exception(NodeTimeoutError("Bot shutting down"))
            self._pending.clear()

    # ── регистрация / heartbeat ────────────────────────────────────────────

    async def register(
        self,
        ws: WebSocket,
        node_id: str,
        models: list[str],
        capabilities: dict | None = None,
    ) -> None:
        caps = capabilities or {}
        async with self._lock:
            # Если нода уже зарегистрирована на другом WS — старое соединение
            # помечаем как устаревшее (новое перезапишет запись).
            old = self._nodes.get(node_id)
            if old is not None and old.ws is not ws:
                logger.warning("Node %s re-registering on new WS, dropping old", node_id)
                await self._unsafe_drop(node_id)

            n = NodeConn(
                ws=ws,
                node_id=node_id,
                models=list(models),
                capabilities=caps,
                last_seen=time.monotonic(),
                inflight=0,
                max_concurrent=int(caps.get("max_concurrent", 1)) or 1,
                gpu=bool(caps.get("gpu", False)),
            )
            self._nodes[node_id] = n
            for m in models:
                bucket = self._by_model.setdefault(m, [])
                if node_id not in bucket:
                    bucket.append(node_id)
        logger.info("Node registered id=%s models=%s gpu=%s", node_id, models, n.gpu)
        self._snapshot_metric()

    def heartbeat(self, node_id: str, load: dict | None = None) -> None:
        n = self._nodes.get(node_id)
        if n is None:
            logger.debug("Heartbeat from unknown node %s", node_id)
            return
        n.last_seen = time.monotonic()
        if load:
            n.inflight = int(load.get("inflight", n.inflight))
            n.max_concurrent = int(load.get("max", n.max_concurrent)) or n.max_concurrent

    async def handle_result(self, task_id: str, audio_b64: str, fmt: str) -> None:
        task = self._pending.pop(task_id, None)
        if task is None:
            logger.debug("Result for unknown task %s", task_id)
            return
        n = self._nodes.get(task.target_node_id)
        if n is not None:
            n.inflight = max(0, n.inflight - 1)
        if not task.future.done():
            task.future.set_result((audio_b64, fmt))
        self._inc_node_metric(StatsType.NODE_TASKS, "succeeded")
        self._inc_timing(StatsType.NODE_TASK_TIME, task.started_monotonic)

    async def handle_error(self, task_id: str, code: str, message: str) -> None:
        task = self._pending.pop(task_id, None)
        if task is None:
            logger.debug("Error for unknown task %s", task_id)
            return
        n = self._nodes.get(task.target_node_id)
        if n is not None:
            n.inflight = max(0, n.inflight - 1)
        logger.warning("Node task %s failed: %s %s", task_id, code, message)
        if task.attempts < self._max_attempts:
            await self._retry(task, reason=f"node_error:{code}")
        elif not task.future.done():
            self._inc_node_metric(StatsType.NODE_TASKS, "failed")
            task.future.set_exception(NodeTaskError(f"{code}: {message}"))

    # ── диспетчер задач ────────────────────────────────────────────────────

    async def dispatch_task(
        self,
        text: str,
        model: str,
        voice: str = "irina",
        pitch: int = 0,
        fmt: str = "mp3",
        speed: float = 1.0,
    ) -> tuple[bytes, str]:
        """Отправить задачу ноде и дождаться результата.

        :raises NodeUnavailableError: нет онлайн GPU-ноды с моделью.
        :raises NodeTimeoutError: задача не уложилась в task_timeout_s после ретраев.
        :raises NodeTaskError: нода вернула tts:error после ретраев.
        :return: (audio_bytes, content_type)
        """
        future: asyncio.Future[tuple[str, str]] = asyncio.get_event_loop().create_future()
        task = _PendingTask(
            future=future,
            text=text,
            model=model,
            voice=voice,
            pitch=pitch,
            fmt=fmt,
            speed=speed,
            target_node_id="",
            started_monotonic=time.monotonic(),
        )
        self._inc_node_metric(StatsType.NODE_TASKS, "dispatched")
        await self._send_task(task)
        try:
            audio_b64, fmt_out = await asyncio.wait_for(future, timeout=self._task_timeout)
        except TimeoutError:
            self._pop_by_future(future)
            raise NodeTimeoutError(f"Task for model '{model}' timed out after {self._task_timeout}s") from None
        content_type = _content_type(fmt_out)
        return base64.b64decode(audio_b64), content_type

    def _pop_by_future(self, future: asyncio.Future) -> None:
        for tid, task in list(self._pending.items()):
            if task.future is future:
                self._pending.pop(tid, None)
                n = self._nodes.get(task.target_node_id)
                if n is not None:
                    n.inflight = max(0, n.inflight - 1)
                if task.attempts < self._max_attempts:
                    asyncio.create_task(self._retry(task, reason="timeout"))
                else:
                    self._inc_node_metric(StatsType.NODE_TASKS, "timeout")
                return

    async def _send_task(self, task: _PendingTask) -> None:
        node_id = self._pick_node(task.model)
        if node_id is None:
            raise NodeUnavailableError(f"No online GPU-node with model '{task.model}'")
        task.target_node_id = node_id
        task.attempts += 1
        task_id = str(uuid.uuid4())
        self._pending[task_id] = task
        n = self._nodes[node_id]
        n.inflight += 1
        payload = {
            "type": "tts:task",
            "task_id": task_id,
            "model": task.model,
            "voice": task.voice,
            "pitch": task.pitch,
            "text": task.text,
            "response_format": task.fmt,
            "speed": task.speed,
        }
        try:
            await n.ws.send_json(payload)
        except Exception:
            # WS умер в момент отправки — откатываем и пытаемся ретрай.
            self._pending.pop(task_id, None)
            n.inflight = max(0, n.inflight - 1)
            if task.attempts < self._max_attempts:
                await self._retry(task, reason="ws_send_failed")
            else:
                raise NodeUnavailableError("Failed to send task to node") from None

    async def _retry(self, task: _PendingTask, reason: str) -> None:
        logger.info("Retrying task model=%s reason=%s attempt=%s", task.model, reason, task.attempts)
        self._inc_node_metric(StatsType.NODE_TASKS, "requeued")
        try:
            await self._send_task(task)
        except NodeUnavailableError:
            if not task.future.done():
                self._inc_node_metric(StatsType.NODE_TASKS, "failed")
                task.future.set_exception(NodeUnavailableError(f"No node for '{task.model}' after retry"))

    def _pick_node(self, model: str) -> str | None:
        """Наименее загруженная живая GPU-нода с моделью."""
        best: str | None = None
        best_load = 10**9
        for nid in self._by_model.get(model, []):
            n = self._nodes.get(nid)
            if n is None or not n.alive:
                continue
            if not n.gpu:
                continue
            if n.inflight >= n.max_concurrent:
                continue
            if n.inflight < best_load:
                best, best_load = nid, n.inflight
        return best

    # ── drop / sweeper ──────────────────────────────────────────────────────

    async def drop_by_ws(self, ws: WebSocket) -> None:
        async with self._lock:
            target = None
            for nid, n in self._nodes.items():
                if n.ws is ws:
                    target = nid
                    break
            if target is None:
                return
            await self._unsafe_drop(target)

    async def _unsafe_drop(self, node_id: str) -> None:
        """Удалить ноду из реестра. Вызывать под self._lock."""
        n = self._nodes.pop(node_id, None)
        if n is None:
            return
        for m in n.models:
            bucket = self._by_model.get(m, [])
            if node_id in bucket:
                bucket.remove(node_id)
            if not bucket:
                self._by_model.pop(m, None)
        logger.info("Node dropped id=%s", node_id)
        # Переназначаем задачи, висящие на этой ноде.
        for tid, task in list(self._pending.items()):
            if task.target_node_id == node_id:
                self._pending.pop(tid, None)
                n.inflight = max(0, n.inflight - 1)
                if not task.future.done() and task.attempts < self._max_attempts:
                    asyncio.create_task(self._retry(task, reason="node_dropped"))
                elif not task.future.done():
                    self._inc_node_metric(StatsType.NODE_TASKS, "failed")
                    task.future.set_exception(NodeUnavailableError(f"Node '{node_id}' dropped"))

    async def _heartbeat_sweeper(self) -> None:
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            stale: list[str] = []
            async with self._lock:
                for nid, n in self._nodes.items():
                    if now - n.last_seen > self._hb_timeout:
                        stale.append(nid)
                for nid in stale:
                    logger.warning("Node %s heartbeat lost, dropping", nid)
                    await self._unsafe_drop(nid)
            if stale:
                self._snapshot_metric()

    # ── статистика / introspection ─────────────────────────────────────────

    def is_available(self, model: str) -> bool:
        return self._pick_node(model) is not None

    def list_models(self) -> list[str]:
        return [m for m, nodes in self._by_model.items() if nodes]

    def snapshot(self) -> dict[str, int]:
        """Снапшот активных нод (gauge-метрика). Возвращает {'total': N, 'gpu': K}."""
        total = 0
        gpu = 0
        for n in self._nodes.values():
            if not n.alive:
                continue
            total += 1
            if n.gpu:
                gpu += 1
        return {"total": total, "gpu": gpu}

    def _snapshot_metric(self) -> None:
        if self._statistics is None:
            return
        values = self.snapshot()
        self._statistics.set_gauge(StatsType.NODES_ACTIVE, values)

    def _inc_node_metric(self, stat: StatsType, subtype: str, amount: int = 1) -> None:
        if self._statistics is None:
            return
        self._statistics.inc(stat, subtype=subtype, amount=amount)

    def _inc_timing(self, stat: StatsType, start_monotonic: float) -> None:
        if self._statistics is None:
            return
        elapsed_ms = int((time.monotonic() - start_monotonic) * 1000)
        self._statistics.inc_timing(stat, value_ms=elapsed_ms)


def _content_type(fmt: str) -> str:
    return {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "flac": "audio/flac",
        "opus": "audio/ogg",
        "aac": "audio/aac",
        "pcm": "application/octet-stream",
    }.get(fmt, "audio/mpeg")
