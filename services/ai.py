import logging
from collections.abc import Callable

import sqlalchemy as sa
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import GeneratedImage

logger = logging.getLogger(__name__)


class OpenAIClient:
    def __init__(self, db_session_factory: Callable[[], AsyncSession]):
        self._client = None
        self._db_session_factory = db_session_factory

    async def startup(self):
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
        )

    async def get_sticker_or_cached(self, prompt: str, chatter: str, channel: int) -> str:
        async with self._db_session_factory() as session:
            q = sa.select(GeneratedImage).where(GeneratedImage.prompt == prompt).limit(1)
            cached = (await session.execute(q)).scalar_one_or_none()
            if cached:
                logger.info("Get cached image")
                return cached.image

        image = await self.generate_sticker(prompt)

        async with self._db_session_factory() as session:
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
        if "@quantum075" in prompt.lower():
            prompt = prompt.replace("@Quantum075", "character from applied photo")
            result = await self._client.images.edit(
                model="gpt-image-1-mini",
                prompt=f"Generate an image of drawn in cartoon style `{prompt}` with transparent background and the white outline like a sticker",
                # extra_body={"quality": "low"},
                quality="low",
                image=open("static/images/refs/quantum075.png", "rb"),
                size="1024x1024",
                # moderation="auto",
                output_format="png",
            )
        else:
            result = await self._client.images.generate(
                model="gpt-image-1-mini",
                prompt=f"Image of drawn in cartoon style `{prompt}` with transparent background and the white outline like a sticker",
                quality="low",
                size="1024x1024",
                moderation="auto",
                output_format="png",
            )
        return result.data[0].b64_json