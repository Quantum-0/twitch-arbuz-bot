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
from database.models import User, GeneratedImage, CharacterInfo, TwitchUserSettings
from schemas.enums import FileStorageDir, AIStickerModel, AIReferenceUsagePolicy
from services.ai import OpenAIClient
from services.image_resizer import ImageResizer
from services.s3 import FileStorage, FileNotExistError
from services.stickers_processor import StickerProcessor

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

class AIProviderBudgetExceededError(RewardRedemptionProcessingError):
    def __init__(self):
        super().__init__(
            "Закончился бюджет у провайдера (не ваш), "
            "передайте @Quatnum075 чтоб он пополнил баланс, пажаласта"
        )


def _extract_api_message(exc: APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        if (msg := body.get("message")) is not None:
            return str(msg)
        err = body.get("error")
        if isinstance(err, dict):
            if (msg := err.get("message")) is not None:
                return str(msg)
            return str(err)
        if err is not None:
            return str(err)
    return getattr(exc, "message", None) or str(exc)


_MODERATION_KEYWORDS = ("safety system", "safety_violation", "moderation", "content policy", "content_filter")


def _is_moderation_blocked(exc: BadRequestError) -> bool:
    if exc.type == "image_generation_user_error" and exc.code == "moderation_blocked":
        return True
    text = _extract_api_message(exc).lower()
    return any(kw in text for kw in _MODERATION_KEYWORDS)


class OpenAIBadRequestException(RewardRedemptionProcessingError):
    def __init__(self, exc: BadRequestError):
        super().__init__(f"Ошибка при генерации изображения: {_extract_api_message(exc)}. Баллы возвращены!")

class OpenAIAPIStatusErrorException(RewardRedemptionProcessingError):
    def __init__(self, exc: APIStatusError):
        super().__init__(f"Ошибка при генерации изображения: {_extract_api_message(exc)}. Баллы возвращены!")

class UnknownRedemptionProcessingException(RewardRedemptionProcessingError):
    def __init__(self):
        super().__init__(f"Неизвестная ошибка. Баллы возвращены!")


class ForeignReferenceNotAllowedError(RewardRedemptionProcessingError):
    """Гейт чужих референсов на канале (deny / with_my_character) либо character-side veto."""
    def __init__(self, message: str):
        super().__init__(message)


class StickersService:
    def __init__(
        self,
        ai: OpenAIClient,
        img_resizer: ImageResizer,
        db_session_factory: Callable[[], AsyncSession],
        s3: FileStorage,
        sticker_processor: StickerProcessor,
    ) -> None:
        self._ai = ai
        self._resizer = img_resizer
        self._db_session_factory = db_session_factory
        self._s3 = s3
        self._sticker_processor = sticker_processor

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
    async def _generate_sticker(self, prompt: str, files: list[bytes], model: str) -> tuple[bytes, float]:
        logger.debug("Generating sticker by prompt")
        try:
            if files:
                image, cost = await self._ai.generate_sticker_by_refs(prompt=prompt, refs=files, model=model)
            else:
                image, cost = await self._ai.generate_sticker(prompt=prompt, model=model)
        except BadRequestError as exc:
            if _is_moderation_blocked(exc):
                logger.debug("Sticker generation was blocked by AI service moderation")
                raise ModerationBlockedException from exc
            else:
                raise OpenAIBadRequestException(exc) from exc
        except APIStatusError as exc:
            if exc.status_code == 402:
                logger.error("No money Q_Q", exc_info=True)
                raise AIProviderBudgetExceededError from exc
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
    async def _handle_refs_from_prompt(self, prompt: str, channel: User) -> tuple[dict[str, str], list[bytes]]:
        """
        Ищем в промпте указания пользователей, заменяем их на файлы или подробные текстовые описания.
        Применяются два ортогональных гейта:
          A) channel-side: channel.settings.ai_reference_usage_policy гейтит ЧУЖИХ персонажей на канале.
          B) character-side: ai_reference_allow_on_other_channels владельца персонажа (только если @name
             маппится на реального User; кастомные референсы без владельца разрешены).
        Собственный персонаж владельца канала на своём канале всегда разрешён.
        """
        found_names = set(re.findall(r"@(\w+)", prompt))
        if not found_names:
            return {}, []

        search_names = {n.lower() for n in found_names}

        async with self._db_session_factory() as session:
            q = (
                sa.select(CharacterInfo, User.login_name, TwitchUserSettings.ai_reference_allow_on_other_channels)
                .outerjoin(User, sa.func.lower(User.login_name) == CharacterInfo.name)
                .outerjoin(TwitchUserSettings, TwitchUserSettings.user_id == User.id)
                .where(CharacterInfo.name.in_(search_names))
            )
            rows = (await session.execute(q)).all()

        descriptions: dict[str, str] = {}
        s3_tasks = []

        channel_name = channel.login_name.lower()
        channel_is_mentioned = channel_name in search_names
        channel_policy = channel.settings.ai_reference_usage_policy

        for char, owner_login, allow_on_other_channels in rows:
            name_in_db = char.name.lower()
            is_channel_owner = name_in_db == channel_name
            original_name = next((n for n in found_names if n.lower() == name_in_db), char.name)

            if not is_channel_owner:
                # Гейт A — channel-side политика владельца канала.
                if channel_policy == AIReferenceUsagePolicy.DENY:
                    raise ForeignReferenceNotAllowedError(
                        "Стример запретил генерацию стикеров с чужими персонажами"
                    )
                if channel_policy == AIReferenceUsagePolicy.WITH_MY_CHARACTER and not channel_is_mentioned:
                    raise ForeignReferenceNotAllowedError(
                        "Стример запретил генерировать на своём канале стикеры с другими стримерами, "
                        "без участия стримера в стикере"
                    )

                # Гейт B — character-side: владелец персонажа разрешил себя у других?
                # Если @name не маппится на реального User (кастомный референс) — разрешено.
                if owner_login is not None and not allow_on_other_channels:
                    raise ForeignReferenceNotAllowedError(
                        f"Пользователь @{original_name} не разрешил генерировать стикеры с ним на чужих каналах."
                    )

            if char.description:
                descriptions[original_name] = char.description

            if char.file_id:
                s3_tasks.append(self._s3.get_object(f"refs/{char.file_id}.png"))

        refs: list[bytes] = []
        if s3_tasks:
            refs = list(await asyncio.gather(*s3_tasks))

        return descriptions, refs

    async def _prepare_final_prompt(self, prompt: str, characters: dict[str, str], with_files: bool, transparent_background: bool = False) -> str:
        # Пропмт для дешёвой модели
        if transparent_background:
            if characters:
                descriptions_str = "\n\nCharacter descriptions:" + "\n".join(
                    [f"- *@{name}*: {description}" for name, description in characters.items()])
            else:
                descriptions_str = ""

            if with_files:
                return f"Generate an image of drawn in cartoon style `{prompt}` with transparent background and the white outline like a sticker.{descriptions_str}\n\nThe appearance of the characters in the attached files"
            return f"Image of drawn in cartoon style `{prompt}` with transparent background and the white outline like a sticker.{descriptions_str}"

        # Инструкция для идеального хромакея без спецэффектов
        if characters:
            descriptions_str = (
                "\n\n[Character Definitions]\n"
                "The following definitions describe the canonical appearance of the characters. "
                "These descriptions override any assumptions made by the model. "
                "Preserve the described appearance exactly unless the prompt explicitly requests a temporary costume, "
                "facial expression, pose, or other non-permanent change.\n\n"
                + "\n".join(
                    f"@{name}:\n{description}"
                    for name, description in characters.items()
                )
            )
        else:
            descriptions_str = ""

        bg_instruction = (
            "The entire background must be a single, solid, uniform bright chroma key green color. "
            "It must be a completely flat vivid green studio background with absolutely no gradients, "
            "no textures, no patterns, and no lighting shifts. "
            "The character must not cast any shadows onto the background, and there must be no outlines, "
            "borders, or frames around the character."
        )

        character_rules = (
            "[Character Rule]\n"
            "The attached reference images define ONLY the character's identity and appearance, "
            "not the final illustration. Preserve the character's design exactly, including species, "
            "body proportions, facial features, hairstyle, colors, markings, clothing, accessories, "
            "tail, ears, and any other distinctive traits.\n\n"
            "Never copy or imitate the pose, body position, composition, camera angle, framing, "
            "lighting, facial expression, or scene layout from the reference images. "
            "If the prompt does not explicitly specify a pose, invent a new natural pose that fits "
            "the requested situation. The final image should look like a completely new illustration "
            "of the same character, not a traced, edited, or slightly modified version of the reference."
        )

        composition_rules = (
            "[Composition Rule]\n"
            "The final image is intended to be used as a die-cut sticker.\n\n"
            "By default, generate only the character with no surrounding scenery or decorative objects.\n\n"
            "If—and only if—the user's prompt explicitly requests environmental elements "
            "(for example: 'in a forest', 'on a beach', 'inside a bed', 'surrounded by flowers', "
            "'in front of a giant spaceship'), include only those requested elements.\n\n"
            "Do NOT generate a full rectangular background or a complete scene. "
            "Instead, make the environment part of the sticker itself, surrounding or supporting "
            "the character while leaving transparent space around the outside. "
            "The character and any requested objects should together form a single cut-out composition "
            "suitable for a sticker."
        )

        if with_files:
            return (
                f"A high-quality cartoon illustration depicting: `{prompt}`. "
                f"{descriptions_str}\n\n"
                f"{character_rules}\n\n"
                f"{composition_rules}\n\n"
                f"[Background Rule]\n"
                f"{bg_instruction}"
            )

        return (
            f"A high-quality cartoon illustration depicting: `{prompt}`. "
            f"The final image is intended to be used as a die-cut sticker. "
            f"By default, generate only the character with no surrounding scenery or decorative objects. "
            f"If the user's prompt explicitly requests environmental elements, include only those requested elements "
            f"as part of the sticker composition rather than generating a full rectangular background.\n\n"
            f"{descriptions_str}\n\n"
            f"[Background Rule]\n"
            f"{bg_instruction}"
        )

    @tracer.start_as_current_span("Stickers: Build sticker")
    async def build_sticker(self, channel: User, prompt: str, chatter: str) -> FileID:  # success: bool + file_id + error (for chat)
        """
        Принимаем запрос на генерацию стикера из дёрнутой награды
        :return: ID файла
        """
        file_id: FileID

        if not re.search(r"@\w", prompt):
            if cached_file_id := await self._get_cached_by_prompt(prompt):
                return cached_file_id

        if channel.balance <= 0:
            raise NegativeBalanceError

        use_mini_model = channel.settings.ai_sticker_model == AIStickerModel.MINI
        model = "gpt-image-1-mini" if use_mini_model else "gpt-image-2"
        characters, files = await self._handle_refs_from_prompt(prompt, channel)
        prompt_for_call = await self._prepare_final_prompt(prompt, characters, with_files=bool(files), transparent_background=use_mini_model)
        image, cost = await self._generate_sticker(prompt_for_call, files, model=model)
        file_id = uuid4()
        resized_image = await self._resizer.resize(image)
        sticker = resized_image if use_mini_model else await self._sticker_processor.process(resized_image)
        await self._save_to_s3(file_id, data=sticker)
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
