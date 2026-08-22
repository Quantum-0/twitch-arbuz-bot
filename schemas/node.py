"""Pydantic-схемы протокола WebSocket между Ботом (оркестратором) и вычислительными нодами.

Все сообщения — JSON с обязательным полем ``type``. Направления:
- Node → Bot: ``node:register``, ``node:ping``, ``tts:result``, ``tts:error``.
- Bot → Node: ``node:registered``, ``node:pong``, ``tts:task``, ``tts:cancel``.

Транспорт готового аудио — base64 inline в ``tts:result.audio_b64``
(TTS-клипы короткие, ~100-300 КБ в mp3).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── Node → Bot ────────────────────────────────────────────────────────────────


class NodeModelInfo(BaseModel):
    """Описание одной RVC-модели, доступной на ноде."""

    id: str = Field(..., examples=["neco-arc"])
    voice: str = Field(default="irina", description="Piper voice id для этой модели")
    pitch: int = Field(default=0, description="Базовый pitch-сдвиг (полутоны)")
    has_index: bool = Field(default=False, description="Есть ли FAISS .index файл")


class NodeCapabilities(BaseModel):
    tts: bool = True
    rvc: bool = True
    gpu: bool = Field(default=False, description="Нода принимает только GPU (см. tts:task routing)")
    gpu_name: str | None = None
    max_concurrent: int = Field(default=1, ge=1)


class NodeRegisterMessage(BaseModel):
    """``node:register`` — первое сообщение после подключения."""

    type: Literal["node:register"] = "node:register"
    node_id: str = Field(..., description="UUID ноды (генерируется клиентом)")
    version: str = Field(default="1.0.0")
    capabilities: NodeCapabilities = Field(default_factory=NodeCapabilities)
    models: list[NodeModelInfo] = Field(default_factory=list)


class NodePingMessage(BaseModel):
    """``node:ping`` — heartbeat каждые 10с."""

    type: Literal["node:ping"] = "node:ping"
    node_id: str
    ts: int = Field(..., description="Unix epoch (секунды) на стороне ноды")
    load: dict[str, int] = Field(default_factory=dict, description="{'inflight': N, 'max': M}")


class TTSResultMessage(BaseModel):
    """``tts:result`` — готовое аудио (base64)."""

    type: Literal["tts:result"] = "tts:result"
    task_id: str
    format: str = Field(default="mp3")
    audio_b64: str = Field(..., description="Готовое аудио, base64-кодированное")
    duration_ms: int | None = None


class TTSErrorMessage(BaseModel):
    """``tts:error`` — нода не смогла выполнить задачу."""

    type: Literal["tts:error"] = "tts:error"
    task_id: str
    code: str = Field(default="error")
    message: str = Field(default="")


# ── Bot → Node ────────────────────────────────────────────────────────────────


class NodeRegisteredMessage(BaseModel):
    """``node:registered`` — ack от бота после успешной регистрации."""

    type: Literal["node:registered"] = "node:registered"
    node_id: str
    heartbeat_interval_s: int = 10
    heartbeat_timeout_s: int = 20


class NodePongMessage(BaseModel):
    type: Literal["node:pong"] = "node:pong"
    ts: int


class TTSTaskMessage(BaseModel):
    """``tts:task`` — Бот передаёт задачу синтеза ноде."""

    type: Literal["tts:task"] = "tts:task"
    task_id: str
    model: str = Field(..., examples=["neco-arc"])
    voice: str = Field(default="irina")
    pitch: int = 0
    text: str
    response_format: Literal["wav", "mp3", "flac", "opus", "aac", "pcm"] = "mp3"
    speed: float = Field(default=1.0, ge=0.25, le=4.0)


class TTSCancelMessage(BaseModel):
    """``tts:cancel`` — Бот отменил задачу (таймаут / нода ушла)."""

    type: Literal["tts:cancel"] = "tts:cancel"
    task_id: str
    reason: str = Field(default="timeout")
