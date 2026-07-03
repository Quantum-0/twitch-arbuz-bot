import base64
import logging
from collections.abc import Callable

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings

logger = logging.getLogger(__name__)


class OpenAIClient:
    def __init__(self, db_session_factory: Callable[[], AsyncSession]):
        self._client: AsyncOpenAI = None  # type: ignore
        self._db_session_factory = db_session_factory

    async def startup(self):
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
        )

    async def generate_sticker_by_refs(self, prompt: str, refs: list[bytes]) -> tuple[bytes, float]:
        logger.info("Start generating image with refs")
        formatted_refs = [
            (f"image_{i}.png", ref, "image/png") for i, ref in enumerate(refs)
        ]
        result = await self._client.images.edit(
            model="gpt-image-2",
            prompt=prompt,
            quality="low",
            image=formatted_refs,
            size="1024x1024",
            output_format="png",
        )
        return base64.b64decode(result.data[0].b64_json), result.usage.cost_rub

    async def generate_sticker(self, prompt: str) -> tuple[bytes, float]:
        logger.info("Start generating image")
        result = await self._client.images.generate(
            model="gpt-image-2",
            prompt=prompt,
            quality="low",
            size="1024x1024",
            moderation="auto",
            output_format="png",
        )
        return base64.b64decode(result.data[0].b64_json), result.usage.cost_rub