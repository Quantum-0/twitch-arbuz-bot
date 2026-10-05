import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from apscheduler.jobstores.base import JobLookupError
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from twitchAPI.type import TwitchResourceNotFound

from config import settings
from database.models import Base, TelegramSettings, TTSSettings, TwitchUserSettings, User
from exceptions import (
    MADuplicateUserError,
    MAInvalidScopeError,
    MAInvalidTokenError,
    MANoToken,
    MATokenExpiredError,
    MATokenInvalidError,
    MAUserNotFoundError,
)
from schemas.api import StatsType
from schemas.enums import FileStorageDir
from schemas.twitch import (
    EventSubRevocationSchema,
    FollowWebhookSchema,
    PointRewardRedemptionWebhookSchema,
    RaidWebhookSchema,
    StreamOfflineSchema,
    StreamOnlineSchema,
    SubscribeWebhookSchema,
    SubscriptionMessageWebhookSchema,
)
from services.cache import Cache
from services.memes import MemealertsService
from services.memes_v2 import MemealertsOAuthService, MemealertsV2Service
from services.moderation import ModerationService
from services.mqtt import MQTTClient
from services.sse_manager import SSEManager
from services.statistics import StatisticsService
from services.stickers import ModerationBlockedException, RewardRedemptionProcessingError, StickersService
from services.tts import TTSService
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch
from utils.enums import SSEChannel
from utils.tts import clean_tts_text, clean_tts_username, truncate_tts

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Префикс APScheduler-джобы подтверждения окончания стрима (docs/telegram.md):
# ставится в handle_stream_offline, снимается быстрым перезапуском или
# исполняется как подтверждённое окончание.
_DEFERRED_OFFLINE_JOB_PREFIX = "stream_offline_deferred"
# Ретрай подтверждения, если Get Streams недоступен в момент срабатывания джобы
# (live=None): до _DEFERRED_OFFLINE_MAX_ATTEMPTS проверок суммарно
# с интервалом _DEFERRED_OFFLINE_RETRY_MINUTES.
_DEFERRED_OFFLINE_MAX_ATTEMPTS = 3
_DEFERRED_OFFLINE_RETRY_MINUTES = 2

# TODO: убрать после полного перехода пользователей на v2.
# Порог user_id для поэтапного уведомления v1-пользователей о миграции на v2.
# Постепенно увеличиваем, пока не покроем всех.
MEMEALERTS_V1_MIGRATION_USER_ID_THRESHOLD = 2
MEMEALERTS_V1_MIGRATION_MESSAGE = (
    "Добрый день, многоувлажняемый стримлер. Текущая интеграция с мемкоинами по токену более неактуальна, "
    "т.к. мы с коллегами из Memealerts договорились и сделали нативную интеграцию. "
    "Вам необходимо зайти в панель управления ботом после стрима и подключить новую интеграцию, "
    "иначе награда в скором времени перестанет работать. Мяу <3"
)

# Legacy v1 can only help when the streamer's v2 authorization is unusable.
# Business/API errors must keep their original meaning instead of being masked by an old v1 token error.
MEMEALERTS_V2_FALLBACK_ERRORS = (MAInvalidTokenError, MAInvalidScopeError, MANoToken, MATokenExpiredError)


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
        mqtt: MQTTClient | None = None,
        statistics: StatisticsService | None = None,
        cache: Cache | None = None,
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
        self._mqtt = mqtt
        self._statistics = statistics
        self._cache = cache

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
            selectins = [User.settings, User.memealerts, User.links, User.tts, User.telegram]
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

        broadcaster_id = payload.event.to_broadcaster_user_id
        if await self._ssem.has_clients(broadcaster_id, SSEChannel.TWITCH_EVENTS):
            event = json.dumps(
                {"type": "raid", "user": payload.event.from_broadcaster_user_name, "count": payload.event.viewers},
                ensure_ascii=False,
            )
            await self._ssem.broadcast(broadcaster_id, SSEChannel.TWITCH_EVENTS, event)

        if not user_settings.enable_shoutout_on_raid:
            await self._twitch.unsubscribe_raid(subscription_id=payload.subscription.subscription_id)
            logger.warning("Handle raid event from user, who didn't enabled shoutout on raid. Unsubscribed")
            return
        # NB: если shoutout включён, raid-подписка принадлежит shoutout-функции и не отписывается.
        # Overlay не управляет channel.raid самостоятельно в этом случае — после первого же рейда
        # подписка остаётся активной только пока включён enable_shoutout_on_raid.

        await self._twitch.shoutout(user=user, shoutout_to=payload.event.from_broadcaster_user_id)

    @task_wrapper
    @tracer.start_as_current_span("Twitch Eventsub: Follow")
    async def handle_follow(self, payload: FollowWebhookSchema | dict[str, Any]) -> None:
        if isinstance(payload, dict):
            payload = FollowWebhookSchema.model_validate(payload, by_name=True)

        broadcaster_id = payload.event.broadcaster_user_id
        if await self._ssem.has_clients(broadcaster_id, SSEChannel.TWITCH_EVENTS):
            event = json.dumps(
                {"type": "follow", "user": payload.event.user_name},
                ensure_ascii=False,
            )
            await self._ssem.broadcast(broadcaster_id, SSEChannel.TWITCH_EVENTS, event)

    @task_wrapper
    @tracer.start_as_current_span("Twitch Eventsub: Subscribe")
    async def handle_subscribe(self, payload: SubscribeWebhookSchema | dict[str, Any]) -> None:
        if isinstance(payload, dict):
            payload = SubscribeWebhookSchema.model_validate(payload, by_name=True)

        broadcaster_id = payload.event.broadcaster_user_id
        if await self._ssem.has_clients(broadcaster_id, SSEChannel.TWITCH_EVENTS):
            event = json.dumps(
                {
                    "type": "sub",
                    "user": payload.event.user_name,
                    "tier": payload.event.tier,
                    "gift": payload.event.is_gift,
                },
                ensure_ascii=False,
            )
            await self._ssem.broadcast(broadcaster_id, SSEChannel.TWITCH_EVENTS, event)

    @task_wrapper
    @tracer.start_as_current_span("Twitch Eventsub: Subscription Message")
    async def handle_subscription_message(self, payload: SubscriptionMessageWebhookSchema | dict[str, Any]) -> None:
        if isinstance(payload, dict):
            payload = SubscriptionMessageWebhookSchema.model_validate(payload, by_name=True)

        broadcaster_id = payload.event.broadcaster_user_id
        if await self._ssem.has_clients(broadcaster_id, SSEChannel.TWITCH_EVENTS):
            event = json.dumps(
                {
                    "type": "resub",
                    "user": payload.event.user_name,
                    "months": payload.event.cumulative_months,
                },
                ensure_ascii=False,
            )
            await self._ssem.broadcast(broadcaster_id, SSEChannel.TWITCH_EVENTS, event)

    async def handle_revocation(self, payload: EventSubRevocationSchema) -> None:
        """EventSub revocation: Twitch отозвал подписку — отключаем соответствующий функционал.

        Приходит с ``Twitch-Eventsub-Message-Type: revocation``, без поля ``event``.
        ``authorization_revoked`` — пользователь отозвал авторизацию приложения,
        ``user_removed`` — удалил аккаунт.
        """
        sub = payload.subscription
        condition = sub.condition
        broadcaster_id = condition.broadcaster_user_id or condition.to_broadcaster_user_id
        logger.warning(
            "EventSub revocation: type=%s, status=%s, broadcaster=%s",
            sub.type,
            sub.status,
            broadcaster_id,
        )
        if broadcaster_id is None:
            logger.error("Revocation без broadcaster_user_id, пропускаем: condition=%s", condition.model_dump())
            return

        if sub.type == "channel.chat.message":
            # Чат-бот больше не получает сообщения канала — отключаем его у стримера.
            await self._disable_chat_bot(broadcaster_id)
            return

        if sub.type == "channel.channel_points_custom_reward_redemption.add":
            # Подписка на награду мертва: проверки в панели сами покажут
            # «Подписка на награду не найдена», вручную стейт не трогаем.
            return

        if sub.type in {"channel.follow", "channel.subscribe", "channel.subscription.message", "channel.raid"}:
            # Оверлейные подписки (heartbeat в Redis, см. routers/api/user/eventsub.py) —
            # убираем запись, чтобы не считались активными.
            if self._cache is not None:
                await self._cache.delete(f"eventsub:overlay:{broadcaster_id}")
            return

        # stream.online / stream.offline: уведомления просто прекратятся, лога выше достаточно.

    async def _disable_chat_bot(self, broadcaster_id: int) -> None:
        """Отключить чат-бот стримеру (EventSub chat.message отозван)."""
        async with self._db_session_factory() as db:
            result = await db.execute(
                sa.update(TwitchUserSettings)
                .values(enable_chat_bot=False)
                .where(TwitchUserSettings.user_id.in_(sa.select(User.id).where(User.twitch_id == str(broadcaster_id))))
                .returning(TwitchUserSettings.user_id)
            )
            disabled = result.scalars().all()
            await db.commit()
        if disabled:
            logger.warning("Чат-бот отключён из-за EventSub revocation: twitch_id=%s", broadcaster_id)

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
            # Ожидаемая гонка: redemption уже отменён/выполнен на стороне Twitch
            # (стример разрешил вручную или повторная доставка вебхука) — не шумим в GlitchTip.
            logger.warning(
                "Redemption уже разрешён на стороне Twitch, отмена пропущена: reward=%s redemption=%s",
                payload.subscription.condition.reward_id,
                payload.event.redemption_id,
            )
            # FIXME: Это НЕ ожидаемое поведение. Управление reward redemption должно осуществляться ботом.
            #  Если вылезла эта ошибка - значит пользователь включил на стороне твича чтоб награда автоматически считалась выполненной
            #  Это не корректно, и нам нужно как-то уведомить пользователя, что награду необходимо отредактировать. Либо сделать это самим. Кстати да, наверно лучше самим отредактировать, убрав галочку "автоматически выполнять"

    async def _fulfill_redemption(self, user: User, payload: PointRewardRedemptionWebhookSchema) -> None:
        try:
            await self._twitch.fulfill_redemption(
                user,
                payload.subscription.condition.reward_id,
                payload.event.redemption_id,
            )
        except TwitchResourceNotFound:
            logger.warning(
                "Redemption уже разрешён на стороне Twitch, подтверждение пропущено: reward=%s redemption=%s",
                payload.subscription.condition.reward_id,
                payload.event.redemption_id,
            )
            # FIXME: Аналогично с предыдущим

    async def reward_buy_memealerts(
        self,
        payload: PointRewardRedemptionWebhookSchema,
        user: User,
    ) -> None:
        self._inc_reward("received", StatsType.REWARD_MEMECOINS)
        try:
            # Deprecated: keep the v1 path and v2-to-v1 fallback until all users migrate to v2.
            # TODO: Remove the v1 branch, fallback, dependency, and legacy token after migration is complete.
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
                except MEMEALERTS_V2_FALLBACK_ERRORS:
                    logger.warning("MemeAlerts v2 authorization failed; trying legacy v1 token", exc_info=True)
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

                # Deprecated v1: уведомляем стримера о необходимости миграции на v2.
                # TODO: убрать после полного перехода пользователей на v2.
                if (
                    user.memealerts.access_token is None
                    and user.memealerts.memealerts_token
                    and user.id < MEMEALERTS_V1_MIGRATION_USER_ID_THRESHOLD
                ):
                    await self._chatbot.send_message(user, MEMEALERTS_V1_MIGRATION_MESSAGE)

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
        except MAUserNotFoundError:
            logger.warning("MA supporter not found: %s", payload.event.user_input)
            self._inc_reward("failed", StatsType.REWARD_MEMECOINS)
            await self._chatbot.send_message(
                user,
                "Пользователь не найден в MemeAlerts. Баллы возвращены. "
                "Проверьте ID или имя; новому зрителю может потребоваться забрать приветственный бонус.",
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
                f"Ошибка начисления мемкоинов: Memealerts не принял установленный токен.",
            )
            await self._cancel_redemption(user, payload)
        except (MAInvalidTokenError, MAInvalidScopeError, MANoToken):
            logger.warning("MA v2 authorization is invalid")
            self._inc_reward("failed", StatsType.REWARD_MEMECOINS)
            await self._chatbot.send_message(
                user,
                f"Ошибка авторизации MemeAlerts. @{user.login_name}, переподключи интеграцию в панели управления ботом.",
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

        await self._maybe_send_sticker_to_telegram(user, sticker_id, payload.event.user_input, payload.event.user_name)

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
            try:
                await self._twitch.send_warning(
                    user,
                    str(payload.event.user_id),
                )
            except Exception:
                logger.error("TTS: failed to send warning to chatter", exc_info=True)
            await self._fulfill_redemption(user, payload)
            return

        text = clean_tts_text(raw_text)
        text = truncate_tts(text, tts.max_length)
        if tts.read_username:
            text = f"{clean_tts_username(payload.event.user_name)} говорит {text}"

        await self._ssem.broadcast(
            int(user.twitch_id),
            SSEChannel.TTS,
            json.dumps({"text": text, "model": tts.model}),
        )
        await self._fulfill_redemption(user, payload)
        self._inc_reward("success", StatsType.TTS_MESSAGES)

    # ── Stream online / offline → Telegram notifications ──────────────────

    _STREAM_ONLINE_REQUEST_PREFIX = "stream_online"
    _STREAM_OFFLINE_REQUEST_PREFIX = "stream_offline"
    _STREAM_OFFLINE_TEXT = "⚪️ Стрим завершён."
    _STREAM_RESTART_TEXT = "🟠 Стрим упал, но был перезапущен."
    # Redis-ключ CD сообщений о рестарте (режим notify): не чаще одного за окно.
    _RESTART_NOTIFY_CD_REDIS_KEY = "telegram:restart_notify_cd"

    @task_wrapper
    @tracer.start_as_current_span("Twitch Eventsub: Stream online")
    async def handle_stream_online(self, payload: StreamOnlineSchema | dict[str, Any]) -> None:
        """stream.online EventSub → отправить уведомление в Telegram-чат стрима.

        Cost = 0, scopes не требуются (app access token).
        request_id = ``stream_online:{user_id}`` — используется для корреляции
        результата (message_id) в ``handle_telegram_result``.

        Если offline-джоба ещё жива (стрим упал < W минут назад и его окончание
        не подтверждено) — это быстрый перезапуск: джоба отменяется, поведение
        по ``stream_restart_behavior`` (см. ``_handle_quick_restart``).
        Иначе — новый стрим, обычное уведомление.

        Заголовок и категория стрима подтягиваются отдельным запросом
        ``GET /helix/streams`` (app access token) — в самом событии stream.online
        этих полей нет (schema v1 содержит только broadcaster + started_at).
        """
        if isinstance(payload, dict):
            payload = StreamOnlineSchema.model_validate(payload, by_name=True)

        user = await self._get_user_by_id_or_login(payload.event.broadcaster_user_id)
        tg: TelegramSettings | None = user.telegram

        if tg is None or not tg.stream_notification_enabled or not tg.stream_chat_id:
            return

        channel_name = payload.event.broadcaster_user_name
        stream_url = f"https://twitch.tv/{payload.event.broadcaster_user_login}"

        # Быстрый перезапуск (< W после stream.offline): отменяем подтверждение окончания.
        if await self._cancel_deferred_stream_offline(user.id):
            await self._handle_quick_restart(user, tg, channel_name, stream_url)
            return

        # Новый стрим — обычное уведомление.
        # stream.online v1 не содержит title/категорию — подтягиваем через Get Streams.
        title, category = await self._fetch_stream_meta(user)

        message_text = self._render_stream_online_message(tg, channel_name, title, category, stream_url)

        # Старый offline-msg больше не актуален (на случай потерянного stream.offline).
        await self._set_stream_message_ids(user.id, online_message_id=tg.last_stream_message_id)

        request_id = f"{self._STREAM_ONLINE_REQUEST_PREFIX}:{user.id}"
        await self._publish_send_message(
            tg.stream_chat_id,
            message_text,
            request_id,
            disable_web_page_preview=not tg.stream_link_preview_enabled,
        )

    async def _cancel_deferred_stream_offline(self, user_id: int) -> bool:
        """Отменить отложенную джобу подтверждения окончания стрима.

        Возвращает True, если джоба существовала — т.е. стрим упал < W минут
        назад и это быстрый перезапуск, а не новый стрим.
        """
        from container_runtime import get_container

        try:
            get_container().scheduler().remove_job(f"{_DEFERRED_OFFLINE_JOB_PREFIX}:{user_id}")
            return True
        except JobLookupError:
            return False

    async def _handle_quick_restart(
        self,
        user: User,
        tg: TelegramSettings,
        channel_name: str,
        stream_url: str,
    ) -> None:
        """Обработать быстрый перезапуск стрима (< W после stream.offline).

        Окончание не подтверждено, offline-действия не применялись, пост о
        начале стрима остаётся на месте. Поведение по ``stream_restart_behavior``
        (docs/telegram.md):

        - ``silent``/``edit`` — тишина: ничего не удаляем и не редактируем,
          будто стрим не прерывался (``edit`` ≡ ``silent``, сохранён для
          совместимости).
        - ``notify`` — сразу отправить сообщение о перезапуске, но не чаще
          одного за окно: Redis-CD ``telegram:restart_notify_cd:{user_id}``
          с TTL = W (анти-спам на серию падений).
        """
        behavior = tg.stream_restart_behavior or "edit"
        chat_id = tg.stream_chat_id
        if not chat_id:  # проверено в handle_stream_online, защита для независимых вызовов
            return
        if behavior != "notify":
            logger.info(
                "Перезапуск стрима (< %d мин) скрыт для user_id=%s (behavior=%s)",
                settings.stream_restart_window_minutes,
                user.id,
                behavior,
            )
            return

        if self._cache is not None:
            cd_key = f"{self._RESTART_NOTIFY_CD_REDIS_KEY}:{user.id}"
            if await self._cache.get_str(cd_key) is not None:
                logger.info("Перезапуск user_id=%s: CD активен, сообщение о рестарте не дублируем", user.id)
                return
            await self._cache.set_str(cd_key, "1", ttl=settings.stream_restart_window_minutes * 60)

        restart_text = self._render_stream_restart_message(tg, channel_name, stream_url)
        await self._publish_send_message(
            chat_id,
            restart_text,
            disable_web_page_preview=not tg.stream_link_preview_enabled,
        )
        logger.info("Перезапуск стрима: отправлено сообщение для user_id=%s", user.id)

    async def _is_stream_live(self, user: User) -> bool | None:
        """Проверить через Get Streams, что стрим сейчас идёт.

        Возвращает None при ошибке API — статус неизвестен (не подтверждён
        ни офлайн, ни онлайн): деструктивные действия не выполняем.
        """
        try:
            streams = await self._twitch.get_streams([user])
        except Exception:
            logger.warning("_is_stream_live: ошибка Get Streams для user_id=%s", user.id, exc_info=True)
            return None
        return streams.get(user) is not None

    @staticmethod
    def _render_stream_restart_message(tg: TelegramSettings, streamer: str, link: str) -> str:
        """Сформировать текст уведомления о перезапуске стрима.

        Если задан ``stream_restart_message_template`` — использует его с
        плейсхолдерами ``{streamer}``, ``{link}``. При ошибке форматирования —
        fallback на дефолтный текст.
        """
        template = tg.stream_restart_message_template
        if not template or not template.strip():
            return TwitchEventSubService._STREAM_RESTART_TEXT
        try:
            return template.format(streamer=streamer, link=link)
        except (KeyError, IndexError, ValueError):
            logger.warning(
                "Ошибка форматирования шаблона stream_restart_message_template, использую дефолт. template=%r",
                template,
            )
            return TwitchEventSubService._STREAM_RESTART_TEXT

    @staticmethod
    def _render_stream_online_message(tg: TelegramSettings, streamer: str, title: str, category: str, link: str) -> str:
        """Сформировать текст уведомления о начале стрима.

        Если задан ``stream_message_template`` — использует его с плейсхолдерами
        ``{streamer}``, ``{title}``, ``{category}``, ``{link}``. При ошибке
        форматирования (неизвестный плейсхолдер) — fallback на дефолтный текст.
        """
        default = f"🔴 {streamer} начинает стрим!"
        template = tg.stream_message_template
        if not template or not template.strip():
            lines = [default]
            if title:
                lines.append(title)
            if category:
                lines.append(category)
            lines.append("")
            lines.append(link)
            return "\n".join(lines)
        try:
            return template.format(streamer=streamer, title=title, category=category, link=link)
        except (KeyError, IndexError, ValueError):
            logger.warning(
                "Ошибка форматирования шаблона stream_message_template, используем дефолт. template=%r",
                template,
            )
            lines = [default]
            if title:
                lines.append(title)
            if category:
                lines.append(category)
            lines.append("")
            lines.append(link)
            return "\n".join(lines)

    async def _fetch_stream_meta(self, user: User) -> tuple[str, str]:
        """Получить title и категорию текущего стрима через ``GET /helix/streams``.

        Возвращает ``("", "")`` если стрим ещё не виден в Helix (бывает задержка
        между событием stream.online и появлением данных в Get Streams) или при
        ошибке API — уведомление всё равно отправляется, но без заголовка.
        """
        try:
            streams = await self._twitch.get_streams([user])
            stream = streams.get(user)
            if stream is None:
                return "", ""
            return stream.title or "", stream.game_name or ""
        except Exception:
            logger.warning("Не удалось получить title/категорию стрима для user_id=%s", user.id, exc_info=True)
            return "", ""

    @task_wrapper
    @tracer.start_as_current_span("Twitch Eventsub: Stream offline")
    async def handle_stream_offline(self, payload: StreamOfflineSchema | dict[str, Any]) -> None:
        """stream.offline EventSub → отложить подтверждение окончания на W минут.

        Ничего не отправляет и не удаляет сразу (docs/telegram.md): ставит
        APScheduler-джобу ``stream_offline_deferred:{user_id}`` в персистентный
        SQL jobstore (переживает рестарт приложения); ``replace_existing=True`` —
        серия падений просто перезапускает таймер. Джоба через W проверит
        Get Streams и только при подтверждённом офлайне выполнит
        ``stream_offline_behavior``:

        - ``delete``  → удалить сообщение о начале стрима (``last_stream_message_id``);
        - ``message`` → отправить сообщение об окончании (request_id =
          ``stream_offline:{user_id}``, message_id сохранится в
          ``last_stream_offline_message_id``);
        - ``keep``    → ничего.

        ``last_stream_message_id`` здесь НЕ чистится — он нужен джобе.
        """
        if isinstance(payload, dict):
            payload = StreamOfflineSchema.model_validate(payload, by_name=True)

        user = await self._get_user_by_id_or_login(payload.event.broadcaster_user_id)
        tg: TelegramSettings | None = user.telegram

        if tg is None or not tg.stream_notification_enabled or not tg.stream_chat_id:
            return

        from container_runtime import get_container

        run_date = datetime.now(UTC) + timedelta(minutes=settings.stream_restart_window_minutes)
        try:
            get_container().scheduler().add_job(
                process_deferred_stream_offline,
                trigger="date",
                run_date=run_date,
                id=f"{_DEFERRED_OFFLINE_JOB_PREFIX}:{user.id}",
                replace_existing=True,
                misfire_grace_time=None,
                kwargs={"user_id": user.id},
            )
        except Exception:
            logger.error(
                "stream.offline user_id=%s: не удалось поставить stream_offline_deferred джобу",
                user.id,
                exc_info=True,
            )
            return
        logger.info(
            "stream.offline user_id=%s: подтверждение окончания отложено на %d мин",
            user.id,
            settings.stream_restart_window_minutes,
        )

    @staticmethod
    def _render_stream_offline_message(tg: TelegramSettings, streamer: str, link: str) -> str:
        """Сформировать текст уведомления об окончании стрима.

        Если задан ``stream_offline_message_template`` — использует его с
        плейсхолдерами ``{streamer}``, ``{link}`` (title/категорию при offline
        взять неоткуда — Get Streams уже пуст). При ошибке форматирования —
        fallback на дефолтный текст.
        """
        template = tg.stream_offline_message_template
        if not template or not template.strip():
            return TwitchEventSubService._STREAM_OFFLINE_TEXT
        try:
            return template.format(streamer=streamer, link=link)
        except (KeyError, IndexError, ValueError):
            logger.warning(
                "Ошибка форматирования шаблона stream_offline_message_template, использую дефолт. template=%r",
                template,
            )
            return TwitchEventSubService._STREAM_OFFLINE_TEXT

    async def _publish_send_message(
        self,
        chat_id: str,
        message_text: str,
        request_id: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> None:
        """Отправить текстовое сообщение в Telegram через MQTT."""
        if self._mqtt is None:
            logger.warning("MQTT не доступен, не могу отправить сообщение в Telegram")
            return

        payload: dict[str, Any] = {
            "request_id": request_id or str(uuid.uuid4()),
            "chat_id": chat_id,
            "message_text": message_text,
        }
        if disable_web_page_preview:
            payload["disable_web_page_preview"] = True
        await self._mqtt.publish("telegram/send_message", payload)

    async def _publish_edit_message(
        self,
        chat_id: str,
        message_id: str,
        message_text: str,
        request_id: str | None = None,
        fallback_message_text: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> None:
        """Отредактировать текст сообщения в Telegram через MQTT.

        Если редактирование не удастся и задан ``fallback_message_text`` —
        TG-сервис отправит его как новое сообщение (результат придёт с тем же
        request_id).
        """
        if self._mqtt is None:
            logger.warning("MQTT не доступен, не могу отредактировать сообщение в Telegram")
            return

        payload: dict[str, Any] = {
            "request_id": request_id or str(uuid.uuid4()),
            "chat_id": chat_id,
            "message_id": message_id,
            "message_text": message_text,
        }
        if fallback_message_text is not None:
            payload["fallback_message_text"] = fallback_message_text
        if disable_web_page_preview:
            payload["disable_web_page_preview"] = True
        await self._mqtt.publish("telegram/edit_message", payload)

    async def _publish_delete_message(self, chat_id: str, message_id: str) -> None:
        """Удалить сообщение в Telegram через MQTT."""
        if self._mqtt is None:
            logger.warning("MQTT не доступен, не могу удалить сообщение в Telegram")
            return

        await self._mqtt.publish(
            "telegram/delete_message",
            {
                "request_id": str(uuid.uuid4()),
                "chat_id": chat_id,
                "message_id": message_id,
            },
        )

    async def _set_stream_message_ids(
        self,
        user_id: int,
        online_message_id: str | None = None,
        offline_message_id: str | None = None,
    ) -> None:
        """Обновить message_id последнего онлайн/оффлайн-сообщения в БД."""
        async with self._db_session_factory() as db:
            await db.execute(
                sa.update(TelegramSettings)
                .where(TelegramSettings.user_id == user_id)
                .values(
                    last_stream_message_id=online_message_id,
                    last_stream_offline_message_id=offline_message_id,
                )
            )
            await db.commit()

    async def _clear_online_message_id(self, user_id: int) -> None:
        """Обнулить только ``last_stream_message_id``.

        ``last_stream_offline_message_id`` не трогаем: его записывает колбэк
        ``handle_telegram_result`` по результату доставки «Завершён», и общая
        очистка могла бы затереть его (race publish → cleanup → callback).
        """
        async with self._db_session_factory() as db:
            await db.execute(
                sa.update(TelegramSettings)
                .where(TelegramSettings.user_id == user_id)
                .values(last_stream_message_id=None)
            )
            await db.commit()

    # ── AI Stickers → Telegram ────────────────────────────────────────────

    async def _maybe_send_sticker_to_telegram(
        self, user: User, sticker_id: uuid.UUID, prompt: str, chatter_name: str
    ) -> None:
        """Отправить ИИ-стикер в Telegram-чат, если интеграция включена.

        Вызывается после успешной генерации стикера и broadcast в SSE.
        Если у юзера ``stickers_enabled=True`` и ``stickers_chat_id`` задан —
        отправляет фото (``send_photo``) или документ (``send_document``) через MQTT.
        TG-микросервис скачивает стикер по публичному URL.
        """
        tg: TelegramSettings | None = user.telegram
        if tg is None or not tg.stickers_enabled or not tg.stickers_chat_id:
            return

        if self._mqtt is None:
            logger.warning("MQTT не доступен, не могу отправить стикер в Telegram")
            return

        sticker_url = f"{settings.base_url}/files/{FileStorageDir.AI_GENERATED_STICKER}/{sticker_id}"
        caption = f"🎨 {chatter_name}: {prompt}"[:1024]
        request_id = f"sticker:{user.id}:{sticker_id}"

        topic = "telegram/send_document" if tg.stickers_mode == "document" else "telegram/send_photo"
        payload_key = "document_url" if tg.stickers_mode == "document" else "photo_url"

        await self._mqtt.publish(
            topic,
            {
                "request_id": request_id,
                "chat_id": tg.stickers_chat_id,
                payload_key: sticker_url,
                "caption": caption,
            },
        )
        logger.info("Стикер отправлен в Telegram для user_id=%s sticker_id=%s", user.id, sticker_id)


async def process_deferred_stream_offline(user_id: int, attempt: int = 1) -> None:
    """APScheduler-джоба ``stream_offline_deferred:{user_id}`` — подтверждённое окончание стрима.

    Ставится в ``TwitchEventSubService.handle_stream_offline`` через W минут после
    stream.offline (docs/telegram.md). Проверяет через Get Streams, что стрим
    действительно офлайн, и только тогда выполняет ``stream_offline_behavior``
    (delete → удалить пост о начале / message → «Завершён» / keep → ничего)
    и чистит ``last_stream_message_id``.

    Если Get Streams недоступен (статус неизвестен) — перезапланируется на
    ``_DEFERRED_OFFLINE_RETRY_MINUTES`` минут, всего не более
    ``_DEFERRED_OFFLINE_MAX_ATTEMPTS`` попыток.

    Top-level функция (не метод класса) — обязательное требование сериализации
    в персистентный SQL jobstore APScheduler.
    """
    from container_runtime import get_container

    container = get_container()
    service: TwitchEventSubService = container.twitch_eventsub_service()

    async with service._db_session_factory() as db:  # noqa: SLF001
        result = await db.execute(sa.select(User).options(selectinload(User.telegram)).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        return
    tg = user.telegram
    if tg is None or not tg.stream_notification_enabled or not tg.stream_chat_id:
        return

    live = await service._is_stream_live(user)  # noqa: SLF001
    if live is None:
        if attempt >= _DEFERRED_OFFLINE_MAX_ATTEMPTS:
            logger.error(
                "stream_offline_deferred: user_id=%s — Get Streams недоступен (%d попыток), "
                "окончание не подтверждено, больше не повторяю",
                user_id,
                attempt,
            )
            return
        retry_at = datetime.now(UTC) + timedelta(minutes=_DEFERRED_OFFLINE_RETRY_MINUTES)
        try:
            container.scheduler().add_job(
                process_deferred_stream_offline,
                trigger="date",
                run_date=retry_at,
                id=f"{_DEFERRED_OFFLINE_JOB_PREFIX}:{user_id}",
                replace_existing=True,
                misfire_grace_time=None,
                kwargs={"user_id": user_id, "attempt": attempt + 1},
            )
        except Exception:
            logger.error(
                "stream_offline_deferred: user_id=%s — не удалось перезапланировать попытку %d",
                user_id,
                attempt + 1,
                exc_info=True,
            )
            return
        logger.warning(
            "stream_offline_deferred: user_id=%s — Get Streams недоступен, окончание не подтверждено, "
            "повтор через %d мин (попытка %d/%d)",
            user_id,
            _DEFERRED_OFFLINE_RETRY_MINUTES,
            attempt + 1,
            _DEFERRED_OFFLINE_MAX_ATTEMPTS,
        )
        return
    if live:
        logger.info(
            "stream_offline_deferred: user_id=%s — стрим снова онлайн (гонка/отменённый рестарт), ничего не делаю",
            user_id,
        )
        return

    behavior = tg.stream_offline_behavior or "keep"
    if behavior == "delete" and tg.last_stream_message_id:
        await service._publish_delete_message(tg.stream_chat_id, tg.last_stream_message_id)  # noqa: SLF001
    elif behavior == "message":
        stream_url = f"https://twitch.tv/{user.login_name}"
        message_text = service._render_stream_offline_message(tg, user.login_name, stream_url)  # noqa: SLF001
        request_id = f"{TwitchEventSubService._STREAM_OFFLINE_REQUEST_PREFIX}:{user_id}"
        await service._publish_send_message(  # noqa: SLF001
            tg.stream_chat_id,
            message_text,
            request_id,
            disable_web_page_preview=not tg.stream_link_preview_enabled,
        )
    # Чистим только online-id: offline-id заполняет колбэк доставки «Завершён».
    await service._clear_online_message_id(user_id)  # noqa: SLF001
    logger.info(
        "stream_offline_deferred: user_id=%s — окончание подтверждено, применён behavior=%s",
        user_id,
        behavior,
    )
