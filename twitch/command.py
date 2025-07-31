import random
from time import time

from database.models import TwitchUserSettings
from twitch.base_commands import SimpleTargetCommand, SavingResultCommand, SimpleCDCommand
from twitch.state_manager import SMParam
from twitch.utils import join_targets


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

class CmdlistCommand(SimpleCDCommand):
    cooldown_timer_per_chat = 120
    cooldown_timer_per_user = 600

    async def _handle(self, channel: str, user: str, message: str) -> str:
        return f"Список команд в чате для этого бота: https://bot.quantum0.ru/cmdlist?streamer={channel}"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return None

    command_name = "cmdlist"
    command_description = "Список команд чата"
    command_aliases = ["cmdlist"]

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return True

class PatCommand(SimpleTargetCommand):
    command_name = "pat"
    command_aliases = ["pat", "patpat", "pat-pat", "пат", "пат-пат", "патпат", "погладить", "гладить"]
    command_description = "Пат-патнуть пользователя по голове ^w^"

    need_target = True
    cooldown_timer = 45
    cooldown_count = 2

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_pat

    async def _handle(self, channel: str, user: str, message: str, targets: list[str]) -> str:
        target = join_targets(targets)
        how_pat = random.choice(["мягко", "аккуратно", "приятно", "нежно", "ласково"])
        how_stroke = random.choice(["легонько", "мягко", "аккуратно", "приятно"])
        if len(targets) == 1:
            variants = [
                f"@{user} {how_pat} пат-патает {target} по голове",
                f"@{user} {how_pat} пат-патает {target} по головушке",
                f"@{user} {how_pat} делает пат-пат {target}",
                f"@{user} {how_stroke} гладит {target} по голове",
                f"@{user} {how_stroke} гладит по голове {target}",
                f"@{user} хорошенько так патает {target}!",
            ]
        else:
            variants = [
                f"@{user} {how_pat} пат-патает {target} по головам",
                f"@{user} {how_pat} пат-патает {target} по головушкам",
                f"@{user} {how_pat} делает пат-пат {target}",
                f"@{user} {how_stroke} гладит {target} по голове",
                f"@{user} {how_stroke} гладит по голове {target}",
                f"@{user} хорошенько так патает {target}!",
            ]
        return random.choice(variants)

    async def _no_target_reply(self, user: str) -> str | None:
        return f"Чтобы кого-нибудь пат-патнуть, нужно указать, кого именно! Например \"!pat @Quantum075Bot\""

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return f"@{user}, подожди, пожалуйста, {self.delay_to_seconds(delay)}, а то сейчас кому-нибудь лысину сделаешь своими поглаживаниями о:"

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice([f"@{user} с важным видом гладит собственную голову", f"@{user} делает пат-пат себе же",
                              f"Кажется, кому-то не хватает патов! Погладьте @{user} пожалуйста!"])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice(
            [f"Правильно, боты тоже заслуживаниют поглаживаний ^w^", f"@{target} пат-пат тебя, коллега-бот <3"])

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice([f"*довольное мурчание* ^w^", f"уиии, пасипа за пат-пат >w<"])


class HugCommand(SimpleTargetCommand):
    command_name = "hug"
    command_aliases = ["hug", "hugs", "обнять", "обнимать"]
    command_description = "Заобнимать чаттерса!"

    need_target = True
    cooldown_timer = 25

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_hug

    async def _handle(self, channel: str, user: str, message: str, targets: list[str]) -> str:
        target = join_targets(targets)
        join_to_hugs_str = ""
        if len(targets) == 1:
            last_hug_target = await self._state_manager.get_state(channel=channel, user=target[1:].lower(), command=self.command_name, param=SMParam.LAST_APPLY)
            if last_hug_target and time() - last_hug_target < 20:
                join_to_hugs_str = "присоединяется к обнимашкам и "
        variants = [
            f"@{user}{join_to_hugs_str} обнимает {target}",
            f"@{user}{join_to_hugs_str} крепко обнимает {target}",
            f"@{user}{join_to_hugs_str} набрасывается с объятиями на {target}",
            f"@{user}{join_to_hugs_str} стискивает в объятиях {target}",
            f"@{user}{join_to_hugs_str} заобнимовывает {target}",
        ]
        return random.choice(variants)

    async def _no_target_reply(self, user: str) -> str | None:
        return f"@{user} хочет обнимашек, но не справляется с выбором цели для этого, по-этому обнимает плюшевую акулку"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice([f"@{user} подожди, секундочку, прежде чем обнимать кого-то другого!", f"@{user}, вы пока что в процессе обнимания другого пользователя! Подождите {self.delay_to_seconds(delay)}"])

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice([
            #f"@{user} обхватывает себя руками",
            f"@{user} испытывает тактильный голод, из-за чего пытается обнимать себя. Обнимите @{user}, пожалуйста!",
            f"Awww, @{user}, ну ты чего? Давай хотя бы я тебя обниму o^o !hug @{user}",
            f"@{user} обнимает плюшевую акулу из Икеи",
        ])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice([
            f"Обнимаем боооота! >w<",
            f"Боты тоже заслуживают обнимашек! Обнимаем @{target}!"
         ])

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice([f"Уиии, пасиба за обнимашки!", f"@{user}, обнимаю тебя в ответ! <3"])
