import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from memealerts.types.exceptions import MATokenExpiredError
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from twitchAPI.type import TwitchResourceNotFound

from database.models import Base, TTSSettings, TwitchUserSettings, User
from exceptions import MADuplicateUserError, MATokenInvalidError
from schemas.api import StatsType
from schemas.twitch import PointRewardRedemptionWebhookSchema, RaidWebhookSchema
from services.memes import MemealertsService
from services.memes_v2 import MemealertsOAuthService, MemealertsV2Service
from services.moderation import ModerationService
from services.sse_manager import SSEManager
from services.statistics import StatisticsService
from services.stickers import ModerationBlockedException, RewardRedemptionProcessingError, StickersService
from services.tts import TTSService
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch
from utils.enums import SSEChannel
from utils.tts import clean_tts_text, truncate_tts

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class TwitchEventSubService:
    # startup - subscribe topics if need

    def __init__(
        self,
        twitch: Twitch,
        chatbot: ChatBot,
        ssem: SSEManager,
        db_session_factory: Callable[[], AsyncSession],
        stickers: StickersService,
        memealerts: MemealertsService,
        memealerts_v2: MemealertsV2Service,
        memealerts_auth: MemealertsOAuthService,
        moderation: ModerationService,
        tts_service: TTSService,
        statistics: StatisticsService | None = None,
    ):
        self._twitch = twitch
        self._chatbot = chatbot
        self._ssem = ssem
        self._db_session_factory = db_session_factory
        self._stickers = stickers
        self._memealerts = memealerts
        self._memealerts_v2 = memealerts_v2
        self._memealerts_auth = memealerts_auth
        self._moderation = moderation
        self._tts = tts_service
        self._statistics = statistics

    def _inc_reward(self, subtype: str, type_: StatsType) -> None:
        """Fire-and-forget инкремент счётчика наград (без if-обёрток в вызывающем коде)."""
        if self._statistics is not None:
            self._statistics.inc(type_, subtype=subtype)

    @staticmethod
    def task_wrapper(func):
        async def wrapped(*args, **kwargs):
            asyncio.create_task(func(*args, **kwargs))

        return wrapped

    async def _get_user_by_id_or_login(self, id_or_login: str | int, selectin: list[Base] | None = None) -> User:
        if selectin is None:
            selectins = [User.settings, User.memealerts, User.links, User.tts]
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
            query = query.where(User.login_name == id_or_login.lower())
        else:
            query = query.where(User.twitch_id == str(id_or_login))
        async with self._db_session_factory() as db:
            result = await db.execute(query)
            user = result.scalar_one_or_none()
            if user is None:
                raise Exception(f"User not found: `{id_or_login}`")
        return user

    @task_wrapper
    @tracer.start_as_current_span("Twitch Eventsub: Raid")
    async def handle_raid(self, payload: RaidWebhookSchema | dict[str, Any]) -> None:
        if isinstance(payload, dict):
            payload = RaidWebhookSchema.model_validate(payload, by_name=True)

        user = await self._get_user_by_id_or_login(payload.event.to_broadcaster_user_id)
        user_settings: TwitchUserSettings = user.settings

        if not user_settings.enable_shoutout_on_raid:
            await self._twitch.unsubscribe_raid(subscription_id=payload.subscription.subscription_id)
            logger.warning("Handle raid event from user, who didn't enabled shoutout on raid. Unsubscribed")
            return

        await self._twitch.shoutout(user=user, shoutout_to=payload.event.from_broadcaster_user_id)

    @task_wrapper
    @tracer.start_as_current_span("Twitch Eventsub: Reward redemption")
    async def handle_reward_redemption(
        self,
        payload: PointRewardRedemptionWebhookSchema | dict[str, Any],
    ) -> None:
        if isinstance(payload, dict):
            payload_model = PointRewardRedemptionWebhookSchema.model_validate(payload, by_name=True)
        else:
            payload_model = payload

        user = await self._get_user_by_id_or_login(payload_model.event.broadcaster_user_id)

        if user.memealerts.memealerts_reward == payload_model.subscription.condition.reward_id:
            await self.reward_buy_memealerts(user=user, payload=payload_model)
        elif user.settings.ai_sticker_reward_id == payload_model.subscription.condition.reward_id:
            try:
                await self.reward_ai_sticker(user=user, payload=payload_model)
            except RewardRedemptionProcessingError as exc:
                if isinstance(exc, ModerationBlockedException):
                    self._inc_reward("failed_on_moderation", StatsType.REWARD_AI_STICKERS)
                await self._chatbot.send_message(user, exc.chatbot_response)
                if exc.cancel_redemption:
                    await self._cancel_redemption(user=user, payload=payload_model)
        elif user.tts is not None and user.tts.tts_reward_id == payload_model.subscription.condition.reward_id:
            try:
                await self.reward_tts(user=user, payload=payload_model)
            except RewardRedemptionProcessingError as exc:
                await self._chatbot.send_message(user, exc.chatbot_response)
                if exc.cancel_redemption:
                    await self._cancel_redemption(user=user, payload=payload_model)

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
        self._inc_reward("received", StatsType.REWARD_MEMECOINS)
        try:
            # TODO: Плавно переходим на memealerts_v2
            if user.memealerts.access_token is None:
                # Старый флоу
                result = await self._memealerts.give_bonus(
                    user.memealerts.memealerts_token,
                    user.login_name,
                    supporter=payload.event.user_input,
                    amount=user.memealerts.coins_for_reward,
                )
            else:
                # Новый флоу
                try:
                    token = await self._memealerts_auth.get_token_of_user(user)
                    result = await self._memealerts_v2.give_bonus(
                        ma_token=token,
                        streamer=user.login_name,
                        supporter=payload.event.user_input,
                        amount=user.memealerts.coins_for_reward,
                    )
                except Exception:
                    logger.error("Error with memealerts v2!", exc_info=True)
                    if not user.memealerts.memealerts_token:
                        raise
                    result = await self._memealerts.give_bonus(
                        user.memealerts.memealerts_token,
                        user.login_name,
                        supporter=payload.event.user_input,
                        amount=user.memealerts.coins_for_reward,
                    )

            if result:
                try:  # TODO: Проверить что работает, потом убрать
                    msg = "Начислен"
                    if user.memealerts.coins_for_reward % 10 == 1 and user.memealerts.coins_for_reward != 11:
                        coins_name = user.memealerts.memecoin_name_accusative or "Мемкоин"
                    elif 1 < user.memealerts.coins_for_reward % 10 < 5 and user.memealerts.coins_for_reward != 11:
                        coins_name = user.memealerts.memecoin_name_genitive or "Мемкоина"
                        msg += "ы"
                    else:
                        coins_name = user.memealerts.memecoin_name_genitive_multiple or "Мемкоинов"
                        msg += "о"

                    msg += f" {user.memealerts.coins_for_reward} {coins_name} для {payload.event.user_input} :з"
                    await self._chatbot.send_message(user, msg)
                except:
                    await self._chatbot.send_message(user, f"Мемкоины для {payload.event.user_name} начислены :з")
                await self._fulfill_redemption(user, payload)
                self._inc_reward("succeed", StatsType.REWARD_MEMECOINS)
            else:
                await self._chatbot.send_message(
                    user,
                    "Ошибка начисления >.< Баллы возвращены 👀. Проверьте имя пользователя на мемалёрте!",
                )
                await self._cancel_redemption(user, payload)
                self._inc_reward("failed", StatsType.REWARD_MEMECOINS)
        except MADuplicateUserError as exc:
            logger.warning(f"Found duplicate MA user = {exc.supporter}")
            self._inc_reward("failed", StatsType.REWARD_MEMECOINS)
            await self._chatbot.send_message(
                user,
                f'Найдено несколько пользователей с именем "{exc.supporter}". Баллы возвращены. Для начисления мемкоинов используйте ID.',
            )
            await self._cancel_redemption(user, payload)
        except MATokenExpiredError:
            logger.warning("MA Token expired")
            self._inc_reward("failed", StatsType.REWARD_MEMECOINS)
            await self._chatbot.send_message(
                user,
                f"Ошибка начисления мемкоинов. @{user.login_name}, истёк срок действия токена. Пожалуйста, обновите токен в панели управления ботом.",
            )
            await self._cancel_redemption(user, payload)
        except MATokenInvalidError:
            logger.warning("MA Token invalid")
            self._inc_reward("failed", StatsType.REWARD_MEMECOINS)
            await self._chatbot.send_message(
                user,
                f"Ошибка начисления мемкоинов: Memealerts не принял установленный токен. @{user.login_name}, перелогинься на сайте Memealerts и обнови токен, пожалуйста.",
            )
            await self._cancel_redemption(user, payload)
        except Exception:
            logger.error("Error handling redemption", exc_info=True)
            self._inc_reward("failed", StatsType.REWARD_MEMECOINS)
            await self._chatbot.send_message(
                user,
                "Непредвиденная ошибка начисления мемкоинов! О.О Баллы возвращены!",
            )
            await self._cancel_redemption(user, payload)

    @tracer.start_as_current_span("Twitch Eventsub: Reward AI Sticker")
    async def reward_ai_sticker(
        self,
        user: User,
        payload: PointRewardRedemptionWebhookSchema,
    ) -> None:
        self._inc_reward("received", StatsType.REWARD_AI_STICKERS)

        if payload.event.user_input.strip() == "":
            await self._chatbot.send_message(user, "Нужно ввести текст награды О: Баллы возвращены!")
            await self._cancel_redemption(user, payload)
            return

        if not await self._ssem.has_clients(int(user.twitch_id), SSEChannel.AI_STICKER):
            logger.warning("No user connected to SSE")
            await self._chatbot.send_message(user, "Оверлей для ИИ стикеров не подключён в OBS. Баллы возвращены!")
            await self._cancel_redemption(user, payload)
            return

        sticker_id = await self._stickers.build_sticker(
            prompt=payload.event.user_input, channel=user, chatter=payload.event.user_login
        )

        await self._ssem.broadcast(
            int(user.twitch_id),
            SSEChannel.AI_STICKER,
            json.dumps({"sticker_file_id": str(sticker_id)}),
        )
        self._inc_reward("success", StatsType.REWARD_AI_STICKERS)

    @tracer.start_as_current_span("Twitch Eventsub: Reward TTS")
    async def reward_tts(
        self,
        user: User,
        payload: PointRewardRedemptionWebhookSchema,
    ) -> None:
        """Обработка награды «TTS». Reward redemption webhook: badges роли
        здесь нет, поэтому per-role матрица для награды не применяется —
        награда доступна всем, если создана.

        Штраф: при блокировке модерацией баллы НЕ возвращаем (fulfill), в чат
        кидаем предупреждение. Успех → озвучка через SSE TTS-оверлея.
        """
        self._inc_reward("received", StatsType.TTS_MESSAGES)

        raw_text = payload.event.user_input.strip()
        if not raw_text:
            await self._chatbot.send_message(user, "TTS: пустой текст награды. Баллы списаны как штраф 🌚")
            await self._fulfill_redemption(user, payload)
            return

        tts: TTSSettings | None = user.tts
        if tts is None or not tts.enabled:
            await self._chatbot.send_message(user, "TTS выключен у стримера. Баллы возвращены.")
            await self._cancel_redemption(user, payload)
            return

        if not await self._ssem.has_clients(int(user.twitch_id), SSEChannel.TTS):
            logger.warning("TTS: no overlay connected")
            await self._chatbot.send_message(user, "TTS-оверлей не подключён в OBS. Баллы возвращены!")
            await self._cancel_redemption(user, payload)
            return

        # Модерация: при бане баллы списываем (fulfill), не возвращаем.
        result = self._moderation.validate(raw_text)
        if result.is_banned:
            self._inc_reward("reward", StatsType.TTS_BLOCKED)
            await self._chatbot.send_message(
                user,
                "⚠️ TTS: сообщение заблокировано модерацией.",
            )
            await self._twitch.send_warning(
                user,
                str(payload.event.user_id),
            )
            await self._fulfill_redemption(user, payload)
            return

        text = clean_tts_text(raw_text)
        text = truncate_tts(text, tts.max_length)
        if tts.read_username:
            text = f"{payload.event.user_name} говорит {text}"

        await self._ssem.broadcast(
            int(user.twitch_id),
            SSEChannel.TTS,
            json.dumps({"text": text, "model": tts.model}),
        )
        await self._fulfill_redemption(user, payload)
        self._inc_reward("success", StatsType.TTS_MESSAGES)
