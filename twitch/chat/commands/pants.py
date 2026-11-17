import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from operator import itemgetter
from time import time

import sqlalchemy as sa
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import PantsDeny, TwitchUserSettings, User
from twitch.chat.base.cooldown_command import SimpleCDCommand
from twitch.state_manager import SMParam, StateManager
from twitch.utils import extract_targets
from utils.misc import call_with_delay, run_in_clean_otel_context

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Ачивка «Вернул своё»: минимум участников, претендующих на трусы стримера.
PANTS_ACHIEVEMENT_MIN_PARTICIPANTS = 5

# Заготовки «типов трусов» для будущих вариантов анонса победителя (TODO)
_PANTS_TYPES_PLAIN = {"красные", "чёрные", "белые", "чистые", "ношенные"}
_PANTS_TYPES_FANCY = {"с сердечками", "кружевные", "семейные", "эротичные"}


def pants_achievement_earned(channel_login: str, target: str, winner: str, participants_count: int) -> bool:
    """Стример выиграл розыгрыш собственных трусов (зрители пытались украсть,
    он поставил «+» и победил) при достаточном числе участников."""
    return (
        participants_count >= PANTS_ACHIEVEMENT_MIN_PARTICIPANTS
        and winner.lower() == target.lower() == channel_login.lower()
    )


def _target_key(target_ids: dict[str, int], name: str) -> str | int:
    """Ключ TARGET_COOLDOWN: twitch-id цели (резолв по имени из снапшота активных,
    см. _handle), фолбэк — имя. По имени ключ коллизится (одинаковые display name)
    и обходится сменой ника."""
    return target_ids.get(name.lower(), name)


class PantsCommand(SimpleCDCommand):
    command_name = "pants"
    command_aliases = ["трусы", "pants"]
    command_description = "Запустить розыгрыш трусов"

    cooldown_timer_per_chat = 3
    cooldown_timer_per_user = 5
    cooldown_timer_per_target = 1200

    def __init__(
        self,
        sm: StateManager,
        send_message: Callable[..., Awaitable[None]],
        db_session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        super().__init__(sm, send_message, db_session_factory)

    async def check_denied(self, *names: str) -> Sequence[PantsDeny]:
        async with self.db_session() as session:
            result = await session.execute(sa.select(PantsDeny).where(PantsDeny.name.in_([n.lower() for n in names])))
            return result.scalars().all()

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_pants

    async def _explicit_target(self, streamer: User, user: str, message: str) -> tuple[str | None, str | None]:
        """Явная цель «!трусы @имя»: (target, None) при успехе, (None, ответ-отказ) при ошибке,
        (None, None) — цель не указана (сообщение без «@...»)."""
        if not message.lower().startswith("!трусы @"):
            return None, None
        targets = await extract_targets(
            message,
            streamer.login_name,
            user,
            partial(self.chat_bot.get_random_active_user, streamer),
            partial(self.chat_bot._user_list_manager.get_active_users, streamer.login_name),
        )  # TODO replace with display name
        logger.info(f"Target = {targets}")
        if not targets:
            # «!трусы @» без ника: extract_targets отбрасывает «@» — раньше падал IndexError
            return (
                None,
                'Не вижу такого пользователя :< Разыгрывать трусы можно только тех людей, кто писал в чатик ^^"',
            )
        if len(targets) > 1:
            return (
                None,
                "Для розыгрыша трусов нужно выбрать только одну цель! Нельзя утаскивать трусы у людей массово, это неприлично!",
            )
        return targets[0][1:], None

    async def _handle(self, streamer: User, user: str, message: str) -> str | None:
        logger.info("Handle pants raffle")

        # Проверка — идёт ли уже розыгрыш
        pants_user = await self._state_manager.get_state(
            channel=streamer.login_name, command=self.command_name, param=SMParam.USER
        )
        if pants_user:
            logger.info("Cancel, because raffle is active on channel")
            return f"Невозможно начать новый розыгрыш трусов, пока не разыграли трусы @{pants_user}"

        # Берём активных чаттерсов: имя + twitch-id (ключ TARGET_COOLDOWN — по id,
        # чтобы одинаковые display name не склеивались, а смена ника не обходила КД)
        active_entries: list[tuple[str, int]] = await self.chat_bot.get_last_active_users_with_ids(streamer.login_name)
        active_users: list[str] = [name for name, _ in active_entries]
        target_ids: dict[str, int] = {name.lower(): twitch_id for name, twitch_id in active_entries}

        # Выбор цели
        target, reject = await self._explicit_target(streamer, user, message)
        if reject:
            return reject

        if target and target.lower() not in {usr.lower() for usr in active_users}:
            return 'Не вижу такого пользователя :< Разыгрывать трусы можно только тех людей, кто писал в чатик ^^"'

        if target and await self.check_denied(target):
            return "К сожалению, данный пользователь запретил разыгрывать свои трусы :с Выбери другую жертву!"

        logger.info(f"Parsed target: {target}")

        # Выбор рандомной цели
        if not target:
            # Проверяем для каждого КД
            users_last_ts: dict[str, float | None] = {
                usr: await self._state_manager.get_state(
                    channel=streamer.login_name,
                    command=self.command_name,
                    user=_target_key(target_ids, usr),
                    param=SMParam.TARGET_COOLDOWN,
                )
                for usr in active_users
            }
            # Фильтруем
            targets = [
                usr
                for usr in active_users
                if (users_last_ts[usr] is None)
                or (time() - (users_last_ts[usr] or 0.0) > self.cooldown_timer_per_target)
            ]
            # Фильтруем запрещённые
            targets = [usr for usr in targets if not (await self.check_denied(usr))]
            # Проверяем что есть такие
            if len(targets) == 0:
                logger.info("No targets")
                users_with_cd = [(k, v) for k, v in users_last_ts.items() if v is not None]
                if not users_with_cd:
                    return "К сожалению, в чате нет пользователей, чьи трусы можно было бы разыграть"
                min_item: tuple[str, float] = min(users_with_cd, key=itemgetter(1))
                min_delta = time() - min_item[1]
                return (
                    f"К сожалению, все трусы текущих чатерсов были разыграны"
                    f" за последние {int(self.cooldown_timer_per_target / 60)} минут,"
                    f" поэтому пока нет трусов для розыгрыша :<"
                    f" Трусы @{min_item[0]} разыграть можно будет через {self.cooldown_timer_per_target - int(min_delta)} секунд!"
                )
            # Берём рандомного
            target = random.choice(targets)
            logger.info(f"Chosen random target: {target}")

        # К этому моменту цель гарантированно выбрана: явная назначена хелпером
        # (иначе вернули отказ), случайная — выше (иначе вернули ответ).
        if target is None:
            return None

        # Проверяем кулдаун для цели
        last_ts = await self._state_manager.get_state(
            channel=streamer.login_name,
            command=self.command_name,
            user=_target_key(target_ids, target),
            param=SMParam.TARGET_COOLDOWN,
        )
        logger.info(
            f"last_ts for target = {last_ts}, time = {time()}, delta = {(time() - last_ts) if last_ts else None}"
        )
        if last_ts and time() - last_ts < self.cooldown_timer_per_target:
            return f"Трусы @{target} уже были недавно разыграны. Давайте позволим @{target} сперва найти и надеть новые трусы, а потом уже разыграем их"

        # Запускаем розыгрыш
        await self._state_manager.set_state(
            channel=streamer.login_name, command=self.command_name, param=SMParam.USER, value=target
        )
        await self._state_manager.set_state(
            channel=streamer.login_name, command=self.command_name, param=SMParam.PARTICIPANTS, value=set()
        )

        # Запускаем асинхронный таймер
        asyncio.create_task(call_with_delay(60, run_in_clean_otel_context(self.finish_raffle(streamer, target))))

        # Ставим кулдаун
        await self._state_manager.set_state(
            value=time(),
            channel=streamer.login_name,
            command=self.command_name,
            user=_target_key(target_ids, target),
            param=SMParam.TARGET_COOLDOWN,
        )
        return f"Внимание, объявляется розыгрыш трусов @{target}! Ставьте '+' в чат, чтобы принять участие в розыгрыше!"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return ""

    async def finish_raffle(self, channel: User, target: str):
        logger.info(f"Finishing raffle for channel {channel.login_name}")
        with tracer.start_as_current_span("Pants Raffle: Processing Result"):
            target_from_sm = await self._state_manager.get_state(
                channel=channel.login_name, command=self.command_name, param=SMParam.USER
            )
            participants: set[str] = await self._state_manager.get_state(
                channel=channel.login_name, command=self.command_name, param=SMParam.PARTICIPANTS
            )
            logger.info(f"Participants: {participants}")
            if participants is None or not target_from_sm or target_from_sm.lower() != target.lower():
                logger.info("Raffle was canceled")
                return

            participants = set(participants)
            if len(participants) == 0:
                logger.info("Nobody entered")
                await self.send_response(
                    chat=channel,
                    message=f"Розыгрыш окончен! Но, к сожалению, никто не принял участие в розыгрыше твоих трусов, @{target}, поэтому они остаются при тебе :с",
                )
                await self._state_manager.del_state(
                    channel=channel.login_name, command=self.command_name, param=SMParam.USER
                )
                await self._state_manager.del_state(
                    channel=channel.login_name, command=self.command_name, param=SMParam.PARTICIPANTS
                )
                return

            winner: str = random.choice(list(participants))
            logger.info(f"winner = {winner}")

            # Информируем об окончании розыгрыша
            participants_count = len(participants)
            msg = "Розыгрыш трусов окончен! В нашей лотерее "
            if participants_count == 1:
                msg += f"принял участие аж целый {participants_count} человек!"
            elif 1 < participants_count <= 4:
                msg += f"приняло участие аж целых {participants_count} человека!"
            elif participants_count >= 5:
                msg += f"приняло участие аж целых {participants_count} человек!"
            msg += f" Время объявлять победителя! Итак.. Трусы @{target} сегодня получааааает... *барабанная дробь*"
            await self.send_response(chat=channel, message=msg)

        await asyncio.sleep(3)

        with tracer.start_as_current_span("Pants Raffle: Announce Winner"):
            # Объявляем победителя
            if winner.lower() == target.lower():
                logger.info("Winner = self")
                await self.send_response(
                    chat=channel,
                    message=f"@{winner}! Поздравляем, сегодня ты становишься счастливым обладателем собственных трусов! Надевай их скорее обратно! И больше не снимай!",
                )
            else:
                await self.send_response(
                    chat=channel,
                    message=f"@{winner}! Поздравляем, сегодня ты становишься счастливым обладателем трусов @{target}!",
                )

            await self._state_manager.del_state(
                channel=channel.login_name, command=self.command_name, param=SMParam.USER
            )
            await self._state_manager.del_state(
                channel=channel.login_name, command=self.command_name, param=SMParam.PARTICIPANTS
            )
            logger.info("State for pants raffle is erased")

            # Ачивка «Вернул своё»: свой канал, свои трусы, победа при 5+ участниках.
            # После очистки стейтов и под try: сбой ачивки не должен ломать розыгрыш.
            if pants_achievement_earned(channel.login_name, target, winner, participants_count):
                try:
                    from container_runtime import get_container

                    await get_container().achievements().unlock(channel.twitch_id, "pants_kept")
                except Exception:
                    logger.warning("Pants achievement unlock failed", exc_info=True)
