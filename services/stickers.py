import logging
from collections.abc import Callable
from decimal import Decimal
from uuid import UUID, uuid4

from openai import BadRequestError, APIStatusError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import User, GeneratedImage
from schemas.enums import FileStorageDir
from services.ai import OpenAIClient
from services.image_resizer import ImageResizer
from services.s3 import FileStorage, FileNotExistError

import sqlalchemy as sa

logger = logging.getLogger(__name__)

FileID = UUID

class RewardRedemptionProcessingError(Exception):
    def __init__(self, chatbot_response: str, cancel_redemption: bool = True):
        self.chatbot_response = chatbot_response
        self.cancel_redemption = cancel_redemption

class NegativeBalanceError(RewardRedemptionProcessingError):
    def __init__(self):
        super().__init__("Закончились денюшки на генерацию картиночек >.< Стример, пополни баланс, пожалуйста!")

class ModerationBlockedException(RewardRedemptionProcessingError):
    def __init__(self):
        super().__init__("Запрос отклонён системой модерации как небезопасный для стрима. Баллы возвращены!")

class OpenAIBadRequestException(RewardRedemptionProcessingError):
    def __init__(self, exc: BadRequestError):
        super().__init__(f"Ошибка при генерации изображения: {exc.type} {exc.code}. Баллы возвращены!")

class OpenAIAPIStatusErrorException(RewardRedemptionProcessingError):
    def __init__(self, exc: APIStatusError):
        super().__init__(f"Ошибка при генерации изображения: {exc.type} {exc.code}. Баллы возвращены!")

class UnknownRedemptionProcessingException(RewardRedemptionProcessingError):
    def __init__(self):
        super().__init__(f"Неизвестная ошибка. Баллы возвращены!")


class StickersService:
    def __init__(
        self,
        ai: OpenAIClient,
        img_resizer: ImageResizer,
        db_session_factory: Callable[[], AsyncSession],
        s3: FileStorage,
    ) -> None:
        self._ai = ai
        self._resizer = img_resizer
        self._db_session_factory = db_session_factory
        self._s3 = s3

    async def _get_cached_by_prompt(self, prompt: str) -> FileID | None:
        logger.debug("Trying to get cached sticker by prompt")
        async with self._db_session_factory() as session:
            q = (
                sa
                .select(GeneratedImage)
                .where(
                    sa.func.lower(GeneratedImage.prompt) == prompt.lower()
                )
                .where(
                    GeneratedImage.created_at >= sa.func.now() - sa.text("interval '7 days'")
                )
                .limit(1)
            )
            cached: GeneratedImage = (await session.execute(q)).scalar_one_or_none()  # type: ignore
            if cached:
                logger.info("Got cached sticker by prompt")
                return cached.file_id
            else:
                logger.debug("No cached sticker by prompt found")
                return None

    async def _generate_sticker(self, prompt) -> tuple[bytes, float]:
        logger.debug("Generating sticker by prompt")
        try:
            image, cost = await self._ai.generate_sticker(prompt=prompt)
        except BadRequestError as exc:
            if exc.type == 'image_generation_user_error' and exc.code == 'moderation_blocked':
                logger.debug("Sticker generation was blocked by AI service moderation")
                raise ModerationBlockedException from exc
            else:
                raise OpenAIBadRequestException(exc) from exc
        except APIStatusError as exc:
            if exc.status_code == 402:
                logger.error("No money Q_Q", exc_info=True)
            else:
                logger.warning("4XX while generating image", exc_info=True)
            raise OpenAIAPIStatusErrorException(exc) from exc
        except Exception as exc:
            raise UnknownRedemptionProcessingException from exc
        logger.debug("Success sticker generation. Spend: %s", cost)
        return image, cost

    async def _save_to_s3(self, file_id: FileID, data: bytes):
        logger.debug("Saving sticker to s3")
        await self._s3.put_object(f"{FileStorageDir.AI_GENERATED_STICKER}/{file_id}.png", data)

    async def _save_to_db(self, file_id: FileID, prompt: str, chatter: str, channel: User, cost: float):
        logger.debug("Saving sticker to database")
        async with self._db_session_factory() as session, session.begin():
            session.add(
                GeneratedImage(
                    prompt=prompt,
                    by_chatter=chatter,
                    on_channel=int(channel.twitch_id),
                    cost=cost,
                    file_id=file_id,
                )
            )
            channel.total_spent += Decimal(cost * settings.ai_cost_multiplier + settings.ai_cost_single_call)
            session.add(channel)
            await session.commit()

    async def build_sticker(self, channel: User, prompt: str, chatter: str) -> FileID:  # success: bool + file_id + error (for chat)
        """
        Принимаем запрос на генерацию стикеоа из дёрнутой награды
        :return: ID файла
        """
        file_id: FileID

        if cached_file_id := await self._get_cached_by_prompt(prompt):
            return cached_file_id

        if channel.balance <= 0:
            raise NegativeBalanceError

        image, cost = await self._generate_sticker(prompt)
        file_id = uuid4()
        resized_image = await self._resizer.resize(image)
        await self._save_to_s3(file_id, data=resized_image)
        await self._save_to_db(file_id, prompt, chatter, channel, cost)
        return file_id

    async def get_unshown(self, channel: int) -> FileID | None:
        """
        Запрос неотображённых стикеров
        :param channel:
        :return:
        """
        logger.debug("Check for unshown stickers")
        q = (
            sa.select(GeneratedImage)
            .where(GeneratedImage.on_channel == channel)
            .where(GeneratedImage.shown_at.is_(None))
            .order_by(GeneratedImage.created_at.desc())
            .limit(1)
        )
        async with self._db_session_factory() as session:
            img: GeneratedImage = (await session.execute(q)).scalar_one_or_none()  # type: ignore
            if img and img.file_id.int != 0:
                logger.debug("Send unshown sticker to user")
                return img.file_id
        return None


    async def get_sticker(self, file_id: FileID) -> bytes | None:
        """
        Получение байтов стикера через ручку
        :param file_id:
        :return:
        """
        logger.debug("Getting sticker by id=%s", file_id)
        q_update_shown_at = (
            sa.update(GeneratedImage)
            .values(shown_at=sa.func.now())
            .where(GeneratedImage.file_id == file_id)
            .returning(GeneratedImage)
        )
        try:
            file = await self._s3.get_object(f"{FileStorageDir.AI_GENERATED_STICKER}/{file_id}.png")
            logger.debug("File with sticker id=%s was found", file_id)
        except FileNotExistError:
            file = None
        async with self._db_session_factory() as session:
            (await session.execute(q_update_shown_at)).scalar_one_or_none()
            await session.commit()
        logger.debug("Updated sticker in database as shown")
        return file
