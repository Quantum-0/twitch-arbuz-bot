import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from functools import partial
from operator import itemgetter
from time import time

from sqlalchemy.dialects.postgresql import insert

from database.database import AsyncSessionLocal
from database.models import TwitchUserSettings, User, PantsDeny
from twitch.chat.base.cooldown_command import SimpleCDCommand
from twitch.state_manager import SMParam, StateManager
from twitch.utils import extract_targets

import sqlalchemy as sa


logger = logging.getLogger(__name__)


class PantsCommand(SimpleCDCommand):
    command_name = "pants"
    command_aliases = ["трусы", "pants", "запретитьтрусы", "запреттрусов"]
    command_description = "Запустить розыгрыш трусов"

    cooldown_timer_per_chat = 5
    cooldown_timer_per_user = 10
    cooldown_timer_per_target = 120

    # cooldown_timer_per_chat = 120
    # cooldown_timer_per_user = 300
    # cooldown_timer_per_target = 600

    def __init__(
        self, sm: StateManager, send_message: Callable[..., Awaitable[None]]
    ) -> None:
        super().__init__(sm, send_message)

    async def check_denied(self, *names: str) -> list[str]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                sa.select(PantsDeny)
                .where(PantsDeny.name.in_([n.lower() for n in names]))
            )
            return result.scalars().all()

    async def add_to_denied(self, name: str):
        async with AsyncSessionLocal() as session:
            await session.execute(
                insert(PantsDeny)
                .values({"name": name.lower()})
            )

    async def remove_from_denied(self, name: str):
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa.delete(PantsDeny)
                .where(PantsDeny.name == name.lower())
            )

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_pants

    async def _handle(self, streamer: User, user: str, message: str) -> str | None:
        if "запретитьтрусы" in message or "запреттрусов" in message:
            return await self._handle_deny(streamer, user, message)
        return await self._handle_start_raffle(streamer, user, message)

    async def _handle_deny(self, streamer: User, user: str, message: str) -> str | None:
        logger.info("Handle deny raffle")
        user_is_denied = bool(await self.check_denied(user))
        logger.info(f"User denied = {user_is_denied}")

        if not user_is_denied:
            await self.add_to_denied(user)
            logger.info(f"User {user} deny to raffle their pants")
            return f"Отныне @{user} запрещает использовать свои трусы для розыгрыша!"

        await self.remove_from_denied(user)
        logger.info(f"User {user} allows to raffle their pants")
        return f"@{user} вновь разрешает использовать свои трусы для розыгрыша!"

    async def _handle_start_raffle(self, streamer: User, user: str, message: str) -> str | None:
        logger.info("Handle pants raffle")

        # Проверка — идёт ли уже розыгрыш
        pants_user = await self._state_manager.get_state(
            channel=streamer.login_name, command=self.command_name, param=SMParam.USER
        )
        if pants_user:
            logger.info("Cancel, because raffle is active on channel")
            return f"Невозможно начать новый розыгрыш трусов, пока не разыграли трусы @{pants_user}"

        # Берём активных чаттерсов
        active_users: list[str] = [
            x
            for x, y in await self.chat_bot.get_last_active_users(
                streamer.login_name
            )
        ]

        # Выбор цели
        target: str | None = None
        if message.startswith("!трусы @"):
            targets = await extract_targets(
                message, streamer.login_name, partial(self.chat_bot.get_random_active_user, streamer)
            )  # TODO replace with display name
            logger.info(f"Target = {targets}")
            if len(targets) > 1:
                return "Для розыгрыша трусов нужно выбрать только одну цель! Нельзя утаскивать трусы у людей массово, это неприлично!"
            target = targets[0][1:]

        if target and target.lower() not in {usr.lower() for usr in active_users}:
            return "Не вижу такого пользователя :< Разыгрывать трусы можно только тех людей, кто писал в чатик ^^\""

        if await self.check_denied(target):
            return "К сожалению, данный пользователь запретил разыгрывать свои трусы :с Выбери другую жертву!"

        logger.info(f"Parsed target: {target}")

        # Выбор рандомной цели
        if not target:
            # Проверяем для каждого КД
            users_last_ts: dict[str, float | None] = {
                usr: await self._state_manager.get_state(
                    channel=streamer.login_name,
                    command=self.command_name,
                    user=usr,
                    param=SMParam.TARGET_COOLDOWN,
                ) for usr in active_users
            }
            # Фильтруем
            targets = [usr for usr in active_users if (users_last_ts[usr] is None) or (time() - users_last_ts[usr] > self.cooldown_timer_per_target)]
            # Фильтруем запрещённые
            targets = [usr for usr in targets if not (await self.check_denied(usr))]
            # Проверяем что есть такие
            if len(targets) == 0:
                logger.info(f"No targets")
                min_item: tuple[str, float] = min(users_last_ts.items(), key=itemgetter(1))
                min_delta = time() - min_item[1]
                return (
                    f"К сожалению, все трусы текущих чатерсов были разыграны"
                    f" за последние {self.cooldown_timer_per_target / 60} минут,"
                    f" поэтому пока нет трусов для розыгрыша :<"
                    f" Трусы @{min_item[0]} разыграть можно будет через ${min_delta} секунд!"
                )
            # Берём рандомного
            target = random.choice(targets)
            logger.info(f"Chosen random target: {target}")

        # Проверяем кулдаун для цели
        last_ts = await self._state_manager.get_state(
            channel=streamer.login_name,
            command=self.command_name,
            user=target,
            param=SMParam.TARGET_COOLDOWN,
        )
        logger.info(f"last_ts for target = {last_ts}, time = {time()}, delta = {(time() - last_ts) if last_ts else None}")
        if last_ts and time() - last_ts < self.cooldown_timer_per_target:
            return f"Трусы @{target} уже были недавно разыграны. Давайте позволим @{target} сперва найти и надеть новые трусы, а потом уже разыграем их"

        # Запускаем розыгрыш
        await self._state_manager.set_state(channel=streamer.login_name, command=self.command_name, param=SMParam.USER, value=target)
        await self._state_manager.set_state(channel=streamer.login_name, command=self.command_name, param=SMParam.PARTICIPANTS, value=set())
        await self.send_response(chat=streamer, message=f"Внимание, объявляется розыгрыш трусов @{target}! Ставьте '+' в чат, чтобы принять участие в розыгрыше!")

        # Запускаем асинхронный таймер
        asyncio.create_task(self.finish_raffle(streamer, target))

        # Ставим кулдаун
        await self._state_manager.set_state(
            value=time(),
            channel=streamer.login_name,
            command=self.command_name,
            user=target,
            param=SMParam.TARGET_COOLDOWN,
        )

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return ""

    async def finish_raffle(self, channel: User, target: str):
        await asyncio.sleep(60)
        logger.info(f"Finishing raffle for channel {channel.login_name}")
        target_from_sm = await self._state_manager.get_state(channel=channel.login_name, command=self.command_name, param=SMParam.USER)
        participants: set[str] = await self._state_manager.get_state(channel=channel.login_name, command=self.command_name, param=SMParam.PARTICIPANTS)
        logger.info(f"Participants: {participants}")
        if participants is None or not target_from_sm or target_from_sm.lower() != target.lower():
            logger.info("Raffle was canceled")
            return

        if len(participants) == 0:
            logger.info("Nobody entered")
            await self.send_response(chat=channel, message=f"Розыгрыш окончен! Но, к сожалению, никто не принял участие в розыгрыше твоих трусов, @{target}, поэтому они остаются при тебе :с")
            await self._state_manager.del_state(channel=channel.login_name, command=self.command_name,
                                                param=SMParam.USER)
            await self._state_manager.del_state(channel=channel.login_name, command=self.command_name,
                                                param=SMParam.PARTICIPANTS)
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
        await self.send_response(
            chat=channel,
            message=msg
        )

        await asyncio.sleep(3)

        # Объявляем победителя
        type1 = {"красные", "чёрные", "белые", "чистые", "ношенные"}  # TODO
        type2 = {"с сердечками", "кружевные", "семейные", "эротичные"}  # TODO
        if winner.lower() == target.lower():
            logger.info("Winner = self")
            await self.send_response(chat=channel, message=f"@{winner}! Поздравляем, сегодня ты становишься счастливым обладателем собственных трусов! Надевай их скорее обратно! И больше не снимай!")
        else:
            await self.send_response(chat=channel, message=f"@{winner}! Поздравляем, сегодня ты становишься счастливым обладателем трусов @{target}!")

        await self._state_manager.del_state(channel=channel.login_name, command=self.command_name, param=SMParam.USER)
        await self._state_manager.del_state(channel=channel.login_name, command=self.command_name, param=SMParam.PARTICIPANTS)
        logger.info("State for pants raffle is erased")
