import asyncio
import logging
import re
from collections.abc import Callable
from decimal import Decimal
from uuid import UUID, uuid4

import sqlalchemy as sa
from openai import BadRequestError, APIStatusError
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import User, GeneratedImage, CharacterInfo
from schemas.enums import FileStorageDir
from services.ai import OpenAIClient
from services.image_resizer import ImageResizer
from services.s3 import FileStorage, FileNotExistError

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

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

    @tracer.start_as_current_span("Stickers: Get cached by prompt")
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

    @tracer.start_as_current_span("Stickers: Generate sticker")
    async def _generate_sticker(self, prompt: str, files: list[bytes]) -> tuple[bytes, float]:
        logger.debug("Generating sticker by prompt")
        try:
            if files:
                image, cost = await self._ai.generate_sticker_by_refs(prompt=prompt, refs=files)
            else:
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

    @tracer.start_as_current_span("Stickers: Save to S3")
    async def _save_to_s3(self, file_id: FileID, data: bytes):
        logger.debug("Saving sticker to s3")
        await self._s3.put_object(f"{FileStorageDir.AI_GENERATED_STICKER}/{file_id}.png", data)

    @tracer.start_as_current_span("Stickers: Save to DB")
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

    @tracer.start_as_current_span("Stickers: Handle refs from prompt")
    async def _handle_refs_from_prompt(self, prompt) -> tuple[dict[str, str], list[bytes]]:
        """
        Ищем в промпте указания пользователей, заменяем их на файлы или подробные текстовые описания.
        :param prompt:
        :return:
        """
        # Ищем упоминания персонажей
        found_names = set(re.findall(r"@(\w+)", prompt))
        if not found_names:
            return {}, []

        search_names = {n.lower() for n in found_names}

        # Вытаскиваем их из БД
        async with self._db_session_factory() as session:
            q = (
                sa.select(CharacterInfo)
                .where(CharacterInfo.name.in_(search_names))
            )
            # Используем scalars(), чтобы получить объекты, а не кортежи
            result = await session.execute(q)
            chars = result.scalars().all()

        # Собираем маппинг описаний и список задач для S3
        descriptions: dict[str, str] = {}
        s3_tasks = []

        # Чтобы сохранить оригинальный регистр из промпта сопоставляем найденные объекты с именами из сета
        for char in chars:
            name_in_db = char.name.lower()
            # Находим, как это имя было написано в промпте
            original_name = next((n for n in found_names if n.lower() == name_in_db), char.name)

            if char.description:
                descriptions[original_name] = char.description

            if char.file_id:
                s3_tasks.append(self._s3.get_object(f"refs/{char.file_id}.png"))

        # Загружаем файлы рефок
        refs: list[bytes] = []
        if s3_tasks:
            refs = list(await asyncio.gather(*s3_tasks))

        return descriptions, refs

    async def _prepare_final_prompt(self, prompt: str, characters: dict[str, str], with_files: bool) -> str:
        if characters:
            descriptions_str = "\n\nCharacter descriptions:" + "\n".join([f"- *@{name}*: {description}" for name, description in characters.items()])
        else:
            descriptions_str = ""

        if with_files:
            return f"Generate an image of drawn in cartoon style `{prompt}` with transparent background and the white outline like a sticker.{descriptions_str}\n\nThe appearance of the characters in the attached files"
        return f"Image of drawn in cartoon style `{prompt}` with transparent background and the white outline like a sticker.{descriptions_str}"

    @tracer.start_as_current_span("Stickers: Build sticker")
    async def build_sticker(self, channel: User, prompt: str, chatter: str) -> FileID:  # success: bool + file_id + error (for chat)
        """
        Принимаем запрос на генерацию стикера из дёрнутой награды
        :return: ID файла
        """
        file_id: FileID

        if cached_file_id := await self._get_cached_by_prompt(prompt):
            return cached_file_id

        if channel.balance <= 0:
            raise NegativeBalanceError

        characters, files = await self._handle_refs_from_prompt(prompt)
        prompt_for_call = await self._prepare_final_prompt(prompt, characters, with_files=bool(files))
        image, cost = await self._generate_sticker(prompt_for_call, files)
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


    async def get_sticker(self, file_id: FileID, mark_as_viewed: bool = False) -> bytes | None:
        """
        Получение байтов стикера через ручку
        :param file_id:
        :param mark_as_viewed:
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
        if mark_as_viewed:
            async with self._db_session_factory() as session:
                (await session.execute(q_update_shown_at)).scalar_one_or_none()
                await session.commit()
            logger.debug("Updated sticker in database as shown")
        return file
