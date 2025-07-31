import random
from abc import ABC, abstractmethod
from collections.abc import Callable
from time import time
from typing import Awaitable

from twitchAPI.chat import ChatMessage

from database.models import TwitchUserSettings
from twitch.state_manager import StateManager, SMParam
from twitch.utils import extract_targets, join_targets


class Command(ABC):
    @property
    @abstractmethod
    def command_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def command_description(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def command_aliases(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        raise NotImplementedError

    def __init__(self, sm: StateManager, send_message: Callable[..., Awaitable[None]]):
        self._state_manager = sm
        self.send_response = send_message
        if len(self.command_aliases) == 0:
            raise RuntimeError("Command has no trigger aliases")

    @abstractmethod
    async def handle(self, channel: str, message: ChatMessage):
        raise NotImplementedError

    @staticmethod
    def delay_to_seconds( delay: float) -> str:
        delay = int(delay)

        tens = (delay // 10 % 10)
        ones = (delay % 10)
        if ones == 1 and tens != 1:
            return f"{delay} секунду"
        if ones in (2, 3, 4) and tens != 1:
            return f"{delay} секунды"
        return f"{delay} секунд"

class SimpleTargetCommand(Command, ABC):
    @property
    @abstractmethod
    def need_target(self) -> bool:
        raise NotImplementedError

    @property
    @abstractmethod
    def cooldown_timer(self) -> int | None:
        return None

    @property
    def cooldown_count(self) -> int:
        return 1

    async def handle(self, channel: str, message: ChatMessage):
        targets = []
        user: str = message.user.display_name
        user_id: int = int(message.user.id)

        if self.need_target:
            targets = extract_targets(message.text, channel)
            if len(targets) == 0 and self.need_target:
                response = await self._no_target_reply(user)
                await self.send_response(chat=channel, message=response)
                return

        last_command_call = await self._state_manager.get_state(
            channel=channel,
            user=user_id,
            command=self.command_name,
            param=SMParam.COOLDOWN
        )
        command_calls_count = await self._state_manager.get_state(
            channel=channel,
            user=user_id,
            command=self.command_name,
            param=SMParam.CALL_COUNT
        )

        if last_command_call and self.cooldown_timer and time() - last_command_call < self.cooldown_timer:
            if self.cooldown_count == 1 or (command_calls_count or 1) > self.cooldown_count:
                delay = self.cooldown_timer - int(time() - last_command_call)
                response = await self._cooldown_reply(user, delay)
                await self.send_response(chat=channel, message=response)
                return
            if self.cooldown_count > 1:
                await self._state_manager.set_state(channel=channel, user=user_id, command=self.command_name, param=SMParam.CALL_COUNT, value=command_calls_count + 1)
        else:
            if self.cooldown_timer:
                await self._state_manager.set_state(channel=channel, user=user_id, command=self.command_name, param=SMParam.COOLDOWN, value=time())
                if self.cooldown_count > 1:
                    await self._state_manager.set_state(channel=channel, user=user_id, command=self.command_name, param=SMParam.CALL_COUNT, value=1)

        if len(targets) == 1 and user.lower() == targets[0][1:].lower():
            response = await self._self_call_reply(user)
            if response:
                await self.send_response(chat=channel, message=response)
                return
        if len(targets) == 1 and targets[0].lower() in {"@streamelements", "@wisebot", "@alurarin", "@nightbot", "@botrixoficial", "@dustyfox_bot", "@moobot"}:
            response = await self._bot_call_reply(user, target=targets[0])
            if response:
                await self.send_response(chat=channel, message=response)
                return
        if len(targets) == 1 and targets[0].lower() == "@quantum075bot":
            response = await self._this_bot_call_reply(user)
            if response:
                await self.send_response(chat=channel, message=response)
                return

        response = await self._handle(channel, user, message.text, targets)
        await self.send_response(chat=channel, message=response)
        return

    @abstractmethod
    async def _no_target_reply(self, user: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def _self_call_reply(self, user: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        raise NotImplementedError

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return await self._bot_call_reply(user, "@Quantum075Bot")

    @abstractmethod
    async def _handle(self, channel: str, user: str, message: str, targets: list[str]) -> str:
        raise NotImplementedError

class SavingResultCommand(Command):
    @property
    @abstractmethod
    def cooldown_timer(self) -> int | None:
        return None

    @property
    @abstractmethod
    def refresh_result_timer(self) -> int | None:
        return None

    @abstractmethod
    async def result_generator(self) -> str:
        raise NotImplementedError

    async def handle(self, channel: str, message: ChatMessage):
        user: str = message.user.display_name
        user_id: int = int(message.user.id)

        last_command_call = await self._state_manager.get_state(
            channel=channel,
            user=user_id,
            command=self.command_name,
            param=SMParam.COOLDOWN
        )
        last_result_value = await self._state_manager.get_state(
            user=user_id,
            command=self.command_name,
            param=SMParam.PREVIOUS_VALUE,
        )
        last_result_time = await self._state_manager.get_state(
            user=user_id,
            command=self.command_name,
            param=SMParam.PREVIOUS_VALUE_TIME,
        )

        targets = extract_targets(message.text, channel)
        if targets:
            response = await self._target_selected(user, targets)
            await self.send_response(chat=channel, message=response)
            return

        if last_command_call and self.cooldown_timer and time() - last_command_call < self.cooldown_timer:
            delay = self.cooldown_timer - int(time() - last_command_call)
            response = await self._cooldown_reply(user, delay)
            await self.send_response(chat=channel, message=response)
            return
        else:
            if self.cooldown_timer:
                await self._state_manager.set_state(channel=channel, user=user_id, command=self.command_name, param=SMParam.COOLDOWN, value=time())

        if self.refresh_result_timer and last_result_time and time() - last_result_time < self.refresh_result_timer:
            response = await self._handle_old(channel, user, message.text, last_result_value, time() - last_result_time)
        else:
            new_value = await self.result_generator()
            await self._state_manager.set_state(user=user_id, command=self.command_name, param=SMParam.PREVIOUS_VALUE, value=new_value)
            await self._state_manager.set_state(user=user_id, command=self.command_name, param=SMParam.PREVIOUS_VALUE_TIME, value=time())
            response = await self._handle_new(channel, user, message.text, new_value)
        await self.send_response(chat=channel, message=response)
        return

    @abstractmethod
    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def _handle_new(self, channel: str, user: str, text: str, new_value: str):
        raise NotImplementedError

    @abstractmethod
    async def _handle_old(self, channel: str, user: str, text: str, old_value: str, seconds_spend: str):
        raise NotImplementedError

    @abstractmethod
    async def _target_selected(self, user: str, targets: list[str]):
        raise NotImplementedError


class BiteCommand(SimpleTargetCommand):
    command_name = "bite"
    command_aliases = ["bite", "кусь", "кусьб", "укусить", "куснуть"]
    command_description = "Укусить пользователя чата"

    need_target = True
    cooldown_timer = 60
    cooldown_count = 3

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_bite

    async def _handle(self, channel: str, user: str, message: str, targets: list[str]) -> str:
        kind_of_bite = ["злобный", "приятный", "мягкий", "нежный", "аккуратный", "агрессивный", "коварный"]
        target_to_bite = ["ухо", "пятку", "хвост", "ногу", "пэрсики", "нос", "плечо", "жёпку"]
        target = join_targets(targets)
        return f"@{user} делает {random.choice(kind_of_bite)} кусь {target} за {random.choice(target_to_bite)}"

    async def _no_target_reply(self, user: str) -> str | None:
        return f"Чтобы укусить кого-то, нужно указать, кого именно кусаешь. Например \"!кусь @{user}\""

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        if random.random() < 0.5:
            return f"@{user}, твои зубки находятся в перезарядке! Подожди чуть-чуть, прежде чем пользоваться командой снова."
        return f"@{user}, твои зубки устали кусаться, подожди {self.delay_to_seconds(delay)}, прежде чем делать новый кусь!"

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice([f"@{user} кусает сам себя о.О", f"@{user} совершает САМОКУСЬ!"])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice([f"{target} простите за беспокойство, коллега-бот, но пользователь @{user} делает вам кусьб"])

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice([f"@{user}, а меня то за что?!", f"Меня кусать нельзя, кусай кого-нибудь другого!", f"Ну капец, уже на ботов своими зубами нападают..", f"@{user}, щас как сам тебя укушу >:c Банхамером!!!"])

class LickCommand(SimpleTargetCommand):
    command_name = "lick"
    command_aliases = ['lick', 'лизь', 'лизнуть', 'облизать']
    command_description = "Облизнуть пользователя чата"

    need_target = True
    cooldown_timer = 60
    cooldown_count = 3

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_bite

    async def _handle(self, channel: str, user: str, message: str, targets: list[str]) -> str:
        target = join_targets(targets)
        random_variants = [
            f'{user} вылизывает всё лицо {target}',
            f'{user} облизывает ухо {target}',
            f'{user} лижет в нос {target}',
            f'{user} пытается лизнуть {target}, но {target} успешно уворачива{"е" if len(targets) == 1 else "ю"}тся от нападения языком!',
        ]
        return random.choice(random_variants)

    async def _no_target_reply(self, user: str) -> str | None:
        if random.random() < 0.05:
            user = "Quantum075"
        return f"Чтобы кого-то лизнуть, нужно указать, кого именно ты хочешь лизнуть. Например \"!лизь @{user}\""

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        random_variants = [
            f'@{user}, твой язык на перезарядке. Прежде чем сделать следующи лизь, подожди {self.delay_to_seconds(delay)}',
            f'@{user}, остановись, язык ж отвалится! Повторный лизь возможен через {self.delay_to_seconds(delay)}',
            f'Язык @{user} устал и не хочет двигаться. Попытка лизнуть оказалась неуспешна. Повторите через {self.delay_to_seconds(delay)}',
        ]
        return random.choice(random_variants)

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice([f"@{user} облизывает сам себя о.О", f"@{user} совершает САМОЛИЗЬ!", f"@{user} развлекается с собственным языком."])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice([f"{target} простите за беспокойство, коллега-бот, но пользователь @{user} вас только что облизнул"])

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice([f"@{user}, о да, давай, облизывай меня, облизывай меня полностью", f"@{user}, вы что себе позволяете?! Это неприлично >.<", f"А-а-а-а-а! Ну мокро же >.<"])

class BananaCommand(SavingResultCommand):
    command_name = "banana"
    command_aliases = ['banan', 'banana', 'банан']
    command_description = "Проанализировать состояние вашего банана"

    cooldown_timer = 10
    cooldown_count = 3

    refresh_result_timer = 5 * 60

    async def result_generator(self) -> str:
        return random.choice(["зелёный", "жёлтый", "мягкий", "nsfw", "длинный", "сгнивший", "заплесневел", "спелый", "сочный", "сладкий"])

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice([f"Мы уже смотрели на твой банан, @{user}. Давай попозже!", "Мы же несколько секунд назад проверяли твой банан, что за нетерпеливость!"])

    async def _handle_new(self, channel: str, user: str, text: str, new_value: str):
        if new_value == "nsfw":
            return f"@{user}, спрячь свой банан обратно, это же неприлично! Трясет тут своим бананом при всех, ну шо такое, ни стыда ни совести!"
        if new_value == "зелёный":
            return f"@{user}, твой банан ещё совсем зелёный"
        return f"@{user}, твой банан - {new_value}"

    async def _target_selected(self, user: str, targets: list[str]):
        variants = [
            f"{user}, а ты зачем чужими бананами интересуешься?",
            f"{user}, ай-яй-яй, неприлично чужие бананы трогать!",
            f"{user}, не трожь чужие бананы!",
        ]
        return random.choice(variants)

    async def _handle_old(self, channel: str, user: str, text: str, old_value: str, seconds_spend: str):
        if old_value == "nsfw":
            return f"Говоришь спрячь свой банан, это неприлично - нет, блин, не слушает, снова достаёт и хвастается перед всеми своим бананом! >_<"
        variants = [
            f"Ну мы же только что смотрели.. Лаадно, давай ещё раз. Твой банан - {old_value}, @{user}",
            f"@{user}, твой банан всё ещё {old_value}",
            f"@{user}, почему тебя так часто беспокоит твой банан? Он всё ещё {old_value}, не беспокойся",
            f"@{user}, он всё ещё {old_value}",
            f"@{user}, думаешь что-то успело так быстро поменяться? Нет, твой банан всё так же {old_value}",
        ]
        return random.choice(variants)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_banana

class BoopCommand(SimpleTargetCommand):
    command_name = "boop"
    command_aliases = ["boop", "буп", "бупнуть"]
    command_description = "Бупнуть пользователя чата в нос :з"

    need_target = True
    cooldown_timer = 60
    cooldown_count = 3

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_boop

    async def _handle(self, channel: str, user: str, message: str, targets: list[str]) -> str:
        target = join_targets(targets)
        if len(targets) == 1 and random.random() < 0.1:
            return f"@{user} делает буп в нось {target}, но {target} внезапно чихает от этого. @{user}, кажется тебе стоит пойти помыть руку.."
        return f"@{user} делает буп в нось {target} !"

    async def _no_target_reply(self, user: str) -> str | None:
        return f"Чтобы бупнуть кого-нибудь в носярку, нужно указать, кого ты хочешь бупнуть! Например \"!буп @{user}\""

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return f"@{user}, подожди {self.delay_to_seconds(delay)}, прежде чем делать бупать снова :з"

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice([f"@{user} тыкает себя пальцем в нос", f"@{user} загадочно ощупывает свой нос о-о\""])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice([f"Прости, @{user}, но мы не можем бупнуть в нось бота. У ботов нет носов О:"])

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice([f"*удивлённо скосил глаза и смотрит на свой нос*", f"{user} нось мой трогаешь? с: И как он тебе?"])
