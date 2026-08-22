"""TTSService — абстрактный интерфейс синтеза речи.

Две реализации:
- ``ApiTTSService`` — прокси к внешнему OpenAI-совместимому TTS API
  (``settings.tts_api_url``), токен хранится серверно. Используется как
  fallback/классический путь (старый сервер на VPS).
- ``NodeTTSService`` — оркестрация P2P-нод через ``NodeManager``:
  задача уходит на GPU-ноду с нужной RVC-моделью по WebSocket.

Переключатель — ``settings.tts_backend`` (``"api"`` / ``"nodes"``),
селектор в ``container.Container.tts_service``.

Пресеты голосовых моделей (``TTS_MODELS``) — общий источник дефолтных
параметров (model/voice/pitch) для обеих реализаций.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from time import monotonic
from typing import Any

import httpx

from config import settings
from schemas.api import StatsType
from services.node_manager import (
    NodeManager,
    NodeTaskError,
    NodeTimeoutError,
    NodeUnavailableError,
)
from services.statistics import StatisticsService

logger = logging.getLogger(__name__)

# ── Пресеты голосовых моделей ────────────────────────────────────
# Ключ пресета → параметры запроса к внешнему TTS API / к ноде.
# В БД/настройках стримера хранится только ключ пресета (TTSSettings.model).
TTS_MODELS: dict[str, dict[str, Any]] = {
    "neco-arc": {
        "label": "Neco-Arc",
        "model": "neco-arc",
        "voice": "irina",
        "pitch": None,
    },
    "gavrilov": {
        "label": "VHS 90-х",
        "model": "gavrilov",
        "voice": "dmitri",
        "pitch": -6,
    },
}
DEFAULT_TTS_MODEL = "neco-arc"


def resolve_tts_model(preset_key: str | None) -> dict[str, Any]:
    """Разрешить ключ пресета в параметры запроса (model/voice/pitch).

    :param preset_key: ключ из TTS_MODELS или None → дефолт.
    :return: dict с ключами model, voice, pitch (всегда присутствуют).
    """
    if preset_key and preset_key in TTS_MODELS:
        return TTS_MODELS[preset_key]
    return TTS_MODELS[DEFAULT_TTS_MODEL]


# ── Абстрактный интерфейс ────────────────────────────────────────


class TTSService(ABC):
    """Базовый интерфейс TTS-бэкенда."""

    def __init__(self, statistics: StatisticsService | None = None) -> None:
        self._statistics = statistics

    @abstractmethod
    async def startup(self) -> None: ...

    async def shutdown(self) -> None:  # noqa: B027
        pass

    @abstractmethod
    async def synthesize(self, text: str, model: str | None = None) -> tuple[bytes, str]:
        """Синтезировать речь.

        :param model: ключ пресета из TTS_MODELS (напр. "neco-arc", "gavrilov").
                      None → дефолт из настроек.
        :return: (audio_bytes, content_type)
        :raises TTSServiceError: при ошибке upstream/ноды.
        """

    def _inc_processing_time(self, start: float) -> None:
        if self._statistics is not None:
            elapsed_ms = int((monotonic() - start) * 1000)
            self._statistics.inc_timing(StatsType.TTS_PROCESSING_TIME, value_ms=elapsed_ms)


# ── API-бэкенд (внешний HTTP TTS) ────────────────────────────────


class ApiTTSService(TTSService):
    """Прокси к внешнему OpenAI-совместимому TTS API (httpx).

    Токен хранится серверно (config.tts_api_token) и никогда не уходит клиенту.
    """

    def __init__(self, statistics: StatisticsService | None = None) -> None:
        super().__init__(statistics)
        self._token: str = ""
        self._url: str = ""
        self._model: str = ""

    async def startup(self) -> None:
        self._token = settings.tts_api_token.get_secret_value()
        self._url = settings.tts_api_url
        if not self._url:
            raise TTSServiceError("TTS service is not configured (no URL)")
        self._model = settings.tts_model

    async def synthesize(self, text: str, model: str | None = None) -> tuple[bytes, str]:
        token = self._token
        if not token:
            raise TTSServiceError("TTS service is not configured (no token)")

        text = text.replace("книга", "книжка")

        preset = resolve_tts_model(model or self._model)
        body: dict[str, Any] = {
            "model": preset["model"],
            "voice": preset["voice"],
            "input": text,
            "response_format": "mp3",
        }
        if preset["pitch"] is not None:
            body["pitch"] = preset["pitch"]

        start = monotonic()
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                resp = await client.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.HTTPError as exc:
            logger.warning("TTS upstream request failed: %s", exc)
            raise TTSServiceError("TTS upstream request failed") from exc

        if resp.status_code != 200:
            logger.warning("TTS upstream returned %s: %s", resp.status_code, resp.text[:200])
            raise TTSServiceError(f"TTS upstream error: {resp.status_code}")

        self._inc_processing_time(start)

        content_type = resp.headers.get("content-type", "audio/mpeg")
        return resp.content, content_type


# ── Nodes-бэкенд (P2P через NodeManager) ────────────────────────


class NodeTTSService(TTSService):
    """TTS через пул вычислительных нод (RVC+Piper на пользовательских GPU).

    ``startup`` не требует URL/токена — достаточно, чтобы ``NodeManager``
    был запущен. ``synthesize`` делегирует задачу ноде с нужной RVC-моделью.
    """

    def __init__(
        self,
        node_manager: NodeManager,
        statistics: StatisticsService | None = None,
    ) -> None:
        super().__init__(statistics)
        self._nm = node_manager

    async def startup(self) -> None:
        if not settings.node_auth_token.get_secret_value():
            logger.warning("NodeTTSService: node_auth_token is empty, nodes will fail to auth")

    async def synthesize(self, text: str, model: str | None = None) -> tuple[bytes, str]:
        text = text.replace("книга", "книжка")
        preset = resolve_tts_model(model or settings.tts_model)
        start = monotonic()
        try:
            audio, content_type = await self._nm.dispatch_task(
                text=text,
                model=preset["model"],
                voice=preset["voice"],
                pitch=preset["pitch"] or 0,
                fmt="mp3",
            )
        except NodeUnavailableError as exc:
            raise TTSServiceError(str(exc)) from exc
        except NodeTimeoutError as exc:
            raise TTSServiceError(str(exc)) from exc
        except NodeTaskError as exc:
            raise TTSServiceError(str(exc)) from exc

        self._inc_processing_time(start)
        return audio, content_type


# ── Общее исключение ────────────────────────────────────────────


class TTSServiceError(Exception):
    """Ошибка синтеза TTS (upstream/конфиг/нода)."""
