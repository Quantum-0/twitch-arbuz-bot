import logging

from openai import AsyncOpenAI

from config import settings
import sqlalchemy as sa

from database.database import AsyncSessionLocal
from database.models import GeneratedImage

logger = logging.getLogger(__name__)


class OpenAIClient:
    def __init__(self):
        self._client = None

    async def startup(self):
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
        )

    async def get_sticker_or_cached(self, prompt: str, chatter: str, channel: int) -> str:
        async with AsyncSessionLocal() as session:
            q = sa.select(GeneratedImage).where(GeneratedImage.prompt == prompt).limit(1)
            cached = (await session.execute(q)).scalar_one_or_none()
            if cached:
                logger.info("Get cached image")
                return cached.image

        image = await self.generate_sticker(prompt)

        async with AsyncSessionLocal() as session:
            async with session.begin():
                session.add(
                    GeneratedImage(
                        prompt=prompt,
                        by_chatter=chatter,
                        on_channel=channel,
                        image=image,
                    )
                )

        return image

    async def generate_sticker(self, prompt: str) -> str:
        logger.info("Start generating image")
        result = await self._client.images.generate(
            model="gpt-image-1-mini",
            prompt=f"Image of drawn `{prompt}` with transparent background",
            quality="low",
            size="1024x1024",
            moderation="auto",
            output_format="png",
        )
        return result.data[0].b64_json