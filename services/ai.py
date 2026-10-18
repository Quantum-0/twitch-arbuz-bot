import base64
import logging
from collections.abc import Callable
from time import monotonic
from typing import Literal

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from schemas.api import StatsType
from services.statistics import StatisticsService

logger = logging.getLogger(__name__)

# Значения параметра background для OpenAI Image API.
ImageBackground = Literal["transparent", "opaque", "auto"]

# Модель API -> subtype timing-метрики AI_STICKER_PROCESSING_TIME.
_GEN_TIMING_SUBTYPES = {
    "gpt-image-1-mini": "gen_mini",
    "gpt-image-2.5-flare": "gen_flare",
    "gpt-image-2.5-sunburst": "gen_sunburst",
}


class OpenAIClient:
    def __init__(
        self,
        db_session_factory: Callable[[], AsyncSession],
        statistics: StatisticsService | None = None,
    ):
        self._client: AsyncOpenAI = None  # type: ignore
        self._db_session_factory = db_session_factory
        self._statistics = statistics

    async def startup(self):
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
        )

    async def generate_sticker_by_refs(
        self,
        prompt: str,
        refs: list[bytes],
        model: str = "gpt-image-2.5-flare",
        background: ImageBackground | None = None,
    ) -> tuple[bytes, float]:
        logger.info("Start generating image with refs")
        formatted_refs = [(f"image_{i}.png", ref, "image/png") for i, ref in enumerate(refs)]
        start = monotonic()
        try:
            result = await self._client.images.edit(
                model=model,
                prompt=prompt,
                quality="low",
                image=formatted_refs,
                size="1024x1024",
                output_format="png",
                background=background,
            )
        finally:
            self._record_gen_timing(model, start)
        return base64.b64decode(result.data[0].b64_json), result.usage.cost_rub

    async def generate_sticker(
        self, prompt: str, model: str = "gpt-image-2.5-flare", background: ImageBackground | None = None
    ) -> tuple[bytes, float]:
        logger.info("Start generating image")
        start = monotonic()
        try:
            result = await self._client.images.generate(
                model=model,
                prompt=prompt,
                quality="low",
                size="1024x1024",
                moderation="auto",
                output_format="png",
                background=background,
            )
        finally:
            self._record_gen_timing(model, start)
        return base64.b64decode(result.data[0].b64_json), result.usage.cost_rub

    def _record_gen_timing(self, model: str, start: float) -> None:
        """Записывает timing замер HTTP-вызова к OpenAI Image API.

        Subtype определяется по имени модели: ``gen_mini`` для gpt-image-1-mini,
        ``gen_flare``/``gen_sunburst`` для gpt-image-2.5-*. Замеряет чистое время
        HTTP-ответа от API (без декодирования base64 и обработки исключений).
        """
        if self._statistics is None:
            return
        elapsed_ms = int((monotonic() - start) * 1000)
        subtype = _GEN_TIMING_SUBTYPES.get(model, "gen_other")
        self._statistics.inc_timing(StatsType.AI_STICKER_PROCESSING_TIME, subtype=subtype, value_ms=elapsed_ms)
