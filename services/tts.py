"""TTSService — прокси к внешнему TTS API с защитой токена.

Токен хранится серверно (config.tts_api_token) и никогда не уходит клиенту.
Оверлей обращается к прокси /overlay/tts/speech, который делегирует сюда.

В будущем здесь же будет списание баланса за генерации.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from time import monotonic

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from schemas.api import StatsType
from services.statistics import StatisticsService

logger = logging.getLogger(__name__)


class TTSService:
    def __init__(
        self,
        db_session_factory: Callable[[], AsyncSession],
        statistics: StatisticsService | None = None,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._statistics = statistics
        self._token: str = ""
        self._url: str = ""
        self._model: str = ""

    async def startup(self) -> None:
        self._token = settings.tts_api_token.get_secret_value()
        self._url = settings.tts_api_url
        if not self._url:
            raise TTSServiceError("TTS service is not configured (no URL)")
        self._model = settings.tts_model

    async def shutdown(self) -> None:
        # httpx.AsyncClient создаётся per-request, отдельного закрывать не нужно.
        pass

    async def synthesize(self, text: str, model: str | None = None) -> tuple[bytes, str]:
        """Синтезировать речь через внешний TTS API.

        :return: (audio_bytes, content_type)
        :raises TTSServiceError: при ошибке upstream.
        """
        token = self._token
        if not token:
            raise TTSServiceError("TTS service is not configured (no token)")

        start = monotonic()
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                resp = await client.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model or self._model,
                        "input": text,
                        "response_format": "mp3",
                    },
                )
        except httpx.HTTPError as exc:
            logger.warning("TTS upstream request failed: %s", exc)
            raise TTSServiceError("TTS upstream request failed") from exc

        if resp.status_code != 200:
            logger.warning("TTS upstream returned %s: %s", resp.status_code, resp.text[:200])
            raise TTSServiceError(f"TTS upstream error: {resp.status_code}")

        if self._statistics is not None:
            elapsed_ms = int((monotonic() - start) * 1000)
            self._statistics.inc_timing(StatsType.TTS_PROCESSING_TIME, value_ms=elapsed_ms)

        content_type = resp.headers.get("content-type", "audio/mpeg")
        return resp.content, content_type


class TTSServiceError(Exception):
    """Ошибка синтеза TTS (upstream/конфиг)."""
