import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from memealerts.types.exceptions import MATokenExpiredError
from openai import BadRequestError, APIStatusError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from twitchAPI.type import TwitchResourceNotFound

from database.models import Base, TwitchUserSettings, User, GeneratedImage
from schemas.enums import FileStorageDir
from schemas.twitch import PointRewardRedemptionWebhookSchema, RaidWebhookSchema
from services.ai import OpenAIClient
from services.image_resizer import ImageResizer
from services.s3 import FileStorage
from services.sse_manager import SSEManager
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch
from utils.enums import SSEChannel
from utils.memes import give_bonus

logger = logging.getLogger(__name__)


class TwitchEventSubService():
    # startup - subscribe topics if need

    def __init__(
        self,
        twitch: Twitch,
        chatbot: ChatBot,
        ai: OpenAIClient,
        ssem: SSEManager,
        img_resizer: ImageResizer,
        db_session_factory: Callable[[], AsyncSession],
        s3: FileStorage,
    ):
        self._twitch = twitch
        self._chatbot = chatbot
        self._ai = ai
        self._ssem = ssem
        # self._img_resizer = img_resizer
        self._db_session_factory = db_session_factory
        self._s3 = s3

    @staticmethod
    def task_wrapper(func):
        async def wrapped(*args, **kwargs):
            asyncio.create_task(
                func(*args, **kwargs)
            )

        return wrapped

    async def _get_user_by_id_or_login(self, id_or_login: str | int, selectin: list[Base] | None = None) -> User:
        if selectin is None:
            selectins = [User.settings, User.memealerts]
        else:
            selectins = selectin

        if not isinstance(id_or_login, int | str) or id_or_login == "":
            raise ValueError

        if isinstance(id_or_login, str) and id_or_login.isdigit():
            id_or_login = int(id_or_login)

        query = sa.Select(User)
        for selectin in selectins:
            query = query.options(selectinload(selectin))
        if isinstance(id_or_login, str):
            query = query.where(User.login_name==id_or_login.lower())
        else:
            query = query.where(User.twitch_id==str(id_or_login))
        async with self._db_session_factory() as db:
            result = await db.execute(query)
            user = result.scalar_one_or_none()
            if user is None:
                raise Exception(f"User not found: `{id_or_login}`")
        return user

    @task_wrapper
    async def handle_raid(self, payload: RaidWebhookSchema | dict[str, Any]) -> None:
        if isinstance(payload, dict):
            payload = RaidWebhookSchema.model_validate(payload, by_name=True)

        user = await self._get_user_by_id_or_login(payload.event.to_broadcaster_user_id)
        user_settings: TwitchUserSettings = user.settings

        if not user_settings.enable_shoutout_on_raid:
            await self._twitch.unsubscribe_raid(
                subscription_id=payload.subscription.subscription_id
            )
            logger.warning("Handle raid event from user, who didn't enabled shoutout on raid. Unsubscribed")
            return

        await self._twitch.shoutout(
            user=user, shoutout_to=payload.event.from_broadcaster_user_id
        )

    @task_wrapper
    async def handle_reward_redemption(
        self,
        payload: PointRewardRedemptionWebhookSchema | dict[str, Any],
    ) -> None:
        if isinstance(payload, dict):
            payload = PointRewardRedemptionWebhookSchema.model_validate(payload, by_name=True)

        user = await self._get_user_by_id_or_login(payload.event.broadcaster_user_id)

        if user.memealerts.memealerts_reward == payload.subscription.condition.reward_id:
            await self.reward_buy_memealerts(user=user, payload=payload)
        elif user.settings.ai_sticker_reward_id == payload.subscription.condition.reward_id:
            # TODO: доп табличка где будет токен и всякое такое, чтоб могли себе тож настроить
            await self.reward_ai_sticker(user=user, payload=payload)

    async def _cancel_redemption(self, user: User, payload: PointRewardRedemptionWebhookSchema) -> None:
        try:
            await self._twitch.cancel_redemption(
                user,
                payload.subscription.condition.reward_id,
                payload.event.redemption_id,
            )
        except TwitchResourceNotFound:
            logger.error("Cannot find redemption to cancel", exc_info=True)

    async def _fulfill_redemption(self, user: User, payload: PointRewardRedemptionWebhookSchema) -> None:
        try:
            await self._twitch.fulfill_redemption(
                user,
                payload.subscription.condition.reward_id,
                payload.event.redemption_id,
            )
        except TwitchResourceNotFound:
            logger.error("Cannot find redemption to fulfill", exc_info=True)

    async def reward_buy_memealerts(
        self,
        payload: PointRewardRedemptionWebhookSchema,
        user: User,
    ) -> None:
        try:
            result = await give_bonus(
                user.memealerts.memealerts_token,
                user.login_name,
                supporter=payload.event.user_input,
                amount=user.memealerts.coins_for_reward,
                db_session_factory=self._db_session_factory,
            )

            if result:
                await self._chatbot.send_message(user, "Мемкоины начислены :з")
                await self._fulfill_redemption(user, payload)
            else:
                await self._chatbot.send_message(
                    user,
                    "Ошибка начисления >.< Баллы возвращены 👀. Проверьте имя пользователя на мемалёрте!",
                )
                await self._cancel_redemption(user, payload)
        except MATokenExpiredError:
            logger.error("MA Token expired")
            await self._chatbot.send_message(
                user,
                f"Ошибка начисления мемкоинов. @{user.login_name}, истёк срок действия токена. Пожалуйста, обновите токен в панели управления ботом.",
            )
            await self._cancel_redemption(user, payload)
        except Exception:
            logger.error("Error handling redemption", exc_info=True)
            await self._chatbot.send_message(
                user,
                "Непредвиденная ошибка начисления мемкоинов! О.О Баллы возвращены!",
            )
            await self._cancel_redemption(user, payload)

    async def reward_ai_sticker(
        self,
        user: User,
        payload: PointRewardRedemptionWebhookSchema,
    ) -> None:
        # TODO: пока только для себя разрешаю:
        if user.login_name not in ["quantum075", "d_e_l_y", "silverosemary"]:
            return

        if payload.event.user_input.strip() == "":
            await self._chatbot.send_message(user, "Нужно ввести текст награды О: Баллы возвращены!")
            await self._cancel_redemption(user, payload)
            return

        if not self._ssem.has_clients(int(user.twitch_id), SSEChannel.AI_STICKER):
            logger.warning("No user connected to SSE")
            await self._chatbot.send_message(user, "Оверлей для ИИ стикеров не подключён в OBS. Баллы возвращены!")
            await self._cancel_redemption(user, payload)
            return

        prompt = payload.event.user_input
        file_id: UUID | None = None
        image: bytes = None  # type: ignore
        cost: float = 0

        # Пытаемся вытащить из Кэша
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
                logger.info("Get cached image")
                file_id = cached.file_id
            else:
                logger.debug("No cached image found")

        # Если закешированного изображения не найдено - пытаемся его сгенерировать
        if not file_id:
            try:
                image, cost = await self._ai.generate_sticker(
                    prompt=payload.event.user_input,
                )
            except BadRequestError as exc:
                if exc.type == 'image_generation_user_error' and exc.code == 'moderation_blocked':
                    await self._chatbot.send_message(user, "Запрос отклонён системой модерации как небезопасный для стрима. Баллы возвращены!")
                else:
                    await self._chatbot.send_message(user, f"Ошибка при генерации изображения: {exc.type} {exc.code}. Баллы возвращены!")
                await self._cancel_redemption(user, payload)
            except APIStatusError as exc:
                if exc.status_code == 402:
                    await self._chatbot.send_message(user, exc.message)
                else:
                    logger.warning("4XX while generating image", exc_info=True)
                await self._cancel_redemption(user, payload)
                raise
            except:
                await self._chatbot.send_message(user,"Неизвестная ошибка. Баллы возвращены!")
                await self._cancel_redemption(user, payload)
                raise

            assert image is not None

            file_id = uuid4()
            await self._s3.put_object(f"{FileStorageDir.AI_GENERATED_STICKER}/{file_id}.png", image)

            async with self._db_session_factory() as session:
                async with session.begin():
                    session.add(
                        GeneratedImage(
                            prompt=prompt,
                            by_chatter=payload.event.user_login,
                            on_channel=int(user.twitch_id),
                            cost=cost,
                            file_id=file_id,
                        )
                    )

        # image = await self._img_resizer.resize(image)
        await self._ssem.broadcast(int(user.twitch_id), SSEChannel.AI_STICKER, json.dumps({"sticker_file_id": str(file_id)}))
