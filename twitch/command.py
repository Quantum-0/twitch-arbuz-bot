import random
from collections.abc import Callable, Awaitable
from time import time

from database.models import TwitchUserSettings, User
from twitch.base_commands import SimpleTargetCommand, SavingResultCommand, SimpleCDCommand
from twitch.state_manager import SMParam, StateManager
from twitch.utils import join_targets, delay_to_seconds, extract_targets


class BiteCommand(SimpleTargetCommand):
    command_name = "bite"
    command_aliases = ["bite", "кусь", "кусьб", "укусить", "куснуть"]
    command_description = "Укусить пользователя чата"

    need_target = True
    cooldown_timer = 45
    cooldown_count = 2

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_bite

    async def _handle(self, streamer: User, user: str, message: str, targets: list[str]) -> str:
        kind_of_bite = ["злобный", "приятный", "мягкий", "нежный", "аккуратный", "агрессивный", "коварный"]
        target_to_bite = ["левое ухо", "правое ухо", "пятку", "хвост", "ногу", "пэрсики", "нос", "плечо", "жёпку", "палец", "животик"]
        target = join_targets(targets)
        # TODO: кусает, делает кусь, кусявкает, покусывает?
        return f"@{user} делает {random.choice(kind_of_bite)} кусь {target} за {random.choice(target_to_bite)}"

    async def _no_target_reply(self, user: str) -> str | None:
        return f"Чтобы укусить кого-то, нужно указать, кого именно кусаешь. Например \"!кусь @{user}\""

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        if random.random() < 0.5:
            return f"@{user}, твои зубки находятся в перезарядке! Подожди чуть-чуть, прежде чем пользоваться командой снова."
        return f"@{user}, твои зубки устали кусаться, подожди {delay_to_seconds(delay)}, прежде чем делать новый кусь!"

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice([f"@{user} кусает сам себя о.О", f"@{user} совершает САМОКУСЬ!"])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice([f"{target} простите за беспокойство, коллега-бот, но пользователь @{user} делает вам кусьб"])

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice([f"@{user}, а меня то за что?!", f"Меня кусать нельзя, кусай кого-нибудь другого!", f"Ну капец, уже на ботов своими зубами нападают..", f"@{user}, щас как сам тебя укушу >:c Банхамером!!!"])


class BushCommand(SimpleTargetCommand):
    command_name = "bush"
    command_aliases = ["куст"]
    command_description = "Опечатка в команде !кусь"

    need_target = True
    cooldown_timer = 120
    cooldown_count = 3

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_bite

    async def _handle(self, streamer: User, user: str, message: str, targets: list[str]) -> str:
        variants = [
            'срывает ветку с куста и кладёт себе на голову, приговаривая "я цвяточек"',
            'запрыгивает в куст и начинает издавать звуки растительности',
            'превращается в куст ' + random.choice(["можжевельника", "мяты", "малины"]),
            f'берёт куст, вытаскивает его из земли и швыряет в стримера',
            'отращивает на себе пару веточек и листочков на них',
            'начинает фотосинтезировать',
        ]
        return f"Опечатавшись в команде !кусь, @{user} внезапно {random.choice(variants)}"

    async def _no_target_reply(self, user: str) -> str | None:
        return await self._handle(None, user, "", [])

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return None

    async def _self_call_reply(self, user: str) -> str | None:
        return await self._handle(None, user, "", [])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return None

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return "Сам ты куст! О.О"



class LickCommand(SimpleTargetCommand):
    command_name = "lick"
    command_aliases = ['lick', 'лизь', 'лизнуть', 'облизать']
    command_description = "Облизнуть пользователя чата"

    need_target = True
    cooldown_timer = 60
    cooldown_count = 3

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_lick

    async def _handle(self, streamer: User, user: str, message: str, targets: list[str]) -> str:
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
            f'@{user}, твой язык на перезарядке. Прежде чем сделать следующи лизь, подожди {delay_to_seconds(delay)}',
            f'@{user}, остановись, язык ж отвалится! Повторный лизь возможен через {delay_to_seconds(delay)}',
            f'Язык @{user} устал и не хочет двигаться. Попытка лизнуть оказалась неуспешна. Повторите через {delay_to_seconds(delay)}',
        ]
        return random.choice(random_variants)

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice([f"@{user} облизывает сам себя о.О", f"@{user} совершает САМОЛИЗЬ!", f"@{user} развлекается с собственным языком."])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice([f"{target} простите за беспокойство, коллега-бот, но пользователь @{user} вас только что облизнул"])

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice([f"@{user}, о да, давай, облизывай меня, облизывай меня полностью", f"@{user}, вы что себе позволяете?! Это неприлично >.<", f"А-а-а-а-а! Ну мокро же >.<"])


class TailCommand(SavingResultCommand):
    command_name = "tail"
    command_aliases = ['tail', 'хвост', 'хвостик']
    command_description = "У вас есть хвост? Так давайте померяем его длину!"

    cooldown_timer = 45

    refresh_result_timer = 10 * 60

    async def result_generator(self, old_value: str | None) -> str:
        if old_value is None:
            return str(random.randint(0, 5000))
        if old_value[0] in ['+', '-']:
            old_value = old_value[1:]
        if random.random() < 0.5:
            new_value = min(5000, int(old_value) + random.randint(1, 250))
            return f"+{new_value}"
        else:
            new_value = max(0, int(old_value) - random.randint(1, 250))
            return f"-{new_value}"

    def convert_tail(self, value: int) -> str:
        if value < 10:
            return f"{value} мм"
        elif value < 100:
            return f"{value / 10} см"
        else: #if value < 1000:
            return f"{value // 100 / 10} м"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice([f"Боюсь, пока рано измерять твой хвост. Он не растёт так быстро!"])

    async def _target_selected(self, user: str, targets: list[str]):
        return None

    async def _handle_new(self, streamer: User, user: str, text: str, new_value: str):
        change = new_value[0] if new_value[0] in ["+", "-"] else None
        value = int(new_value[1:]) if new_value[0] in ["+", "-"] else int(new_value)

        if value < 10:
            result = f"@{user} твой хвост.. стоп.. а где он? А, вот же! Коротенький, всего-лишь {self.convert_tail(int(value))}"
        elif value < 100:
            result = f"@{user}, длина твоего хвоста - {self.convert_tail(int(value))}."
            if random.random() < 0.5:
                result += " Коротенький :3"
            elif change == "+":
                result += " Подрос :з"
            elif change == "-":
                result += " Укоротился >.<"
        elif value < 200:
            result = f"@{user}, длина твоего хвоста - {self.convert_tail(int(value))}."
            if random.random() < 0.5:
                result += " Маленький =w="
            elif change == "+":
                result += " Подрос :з"
            elif change == "-":
                result += " Укоротился >.<"
        elif value < 500:
            result = f"@{user}, длина твоего хвоста - {self.convert_tail(int(value))}."
            if random.random() < 0.5:
                result += " Нормальный такой OwO"
            elif change == "+":
                result += " Подрос :з"
            elif change == "-":
                result += " Укоротился >.<"
        elif value < 1000:
            result = f"@{user}, длина твоего хвоста - {self.convert_tail(int(value))}."
            if random.random() < 0.5:
                result += " Хороооший, большооой!"
            elif change == "+":
                result += " Подрос :з"
            elif change == "-":
                result += " Укоротился >.<"
        elif value < 2500:
            result = f"@{user}, длина твоего хвоста - {self.convert_tail(int(value))}."
            if random.random() < 0.5:
                result += " Нифига себе хвостище!"
            elif change == "+":
                result += " Подрос :з"
            elif change == "-":
                result += " Укоротился >.<"
        elif value <= 5000:
            result = f"@{user}, длина твоего хвоста - {self.convert_tail(int(value))}."
            if random.random() < 0.5:
                result += " Вот это гигант *О*"
            elif change == "+":
                result += " Подрос :з"
            elif change == "-":
                result += " Укоротился >.<"
        return result


    async def _handle_old(self, streamer: User, user: str, text: str, old_value: str, seconds_spend: str):
        variants = [
            f"Ну мы же только что смотрели.. Лаадно, давай ещё раз. Длина твоего хвоста - {self.convert_tail(int(old_value))}, @{user}",
            f"@{user}, твой хвост всё ещё {self.convert_tail(int(old_value))}",
            f"@{user}, думаешь что-то успело так быстро поменяться? Нет, твой хвост всё так же {self.convert_tail(int(old_value))}",
        ]
        return random.choice(variants)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_tail


class BananaCommand(SavingResultCommand):
    command_name = "banana"
    command_aliases = ['banan', 'banana', 'банан']
    command_description = "Проанализировать состояние вашего банана"

    cooldown_timer = 10

    refresh_result_timer = 5 * 60

    async def result_generator(self, old_value: str | None) -> str:
        return random.choice(["зелёный", "жёлтый", "мягкий", "nsfw", "длинный", "сгнивший", "заплесневел", "спелый", "сочный", "сладкий"])

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice([f"Мы уже смотрели на твой банан, @{user}. Давай попозже!", "Мы же несколько секунд назад проверяли твой банан, что за нетерпеливость!"])

    async def _handle_new(self, streamer: User, user: str, text: str, new_value: str):
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

    async def _handle_old(self, streamer: User, user: str, text: str, old_value: str, seconds_spend: str):
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


class HornyGoodCommand(SavingResultCommand):
    command_name = "horny_good"
    command_aliases = ['horny', 'хорни']
    command_description = "Узнать, насколько сильно вам нравится смотреть этот стрим ;)"

    cooldown_timer = 10

    refresh_result_timer = 20 # 3 * 60

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_horny_good

    async def result_generator(self, old_value: str | None) -> str:
        if old_value is None:
            return str(random.randint(0, 100))
        if old_value[0] in ['+', '-']:
            old_value = old_value[1:]
        if random.random() < 0.65 - 0.3 * int(old_value) / 100:
            new_value = min(100, int(old_value) + random.randint(10, 50))
            return f"+{new_value}"
        else:
            new_value = max(0, int(old_value) - random.randint(10, 50))
            return f"-{new_value}"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice([
            f"Солнышко, пирожочек ты наш неприличный, мы же только что смотрели на твоё хорни Потерпи чуть-чуть пожалуйста, пока значение поменяется ;)"
        ])

    async def _handle_new(self, streamer: User, user: str, text: str, new_value: str):
        change = new_value[0] if new_value[0] in ["+", "-"] else None
        value = int(new_value[1:]) if new_value[0] in ["+", "-"] else int(new_value)

        if value < 20:
            if random.random() < 0.5:
                result = f"@{user} хорни всего-лишь на {value}%. Маловато будет, что ж ты так? Расскажи нам о своих вкусах, а мы с чатиком подумаем, как можем помочь тебе :з"
            else:
                result = f"@{user} хорни всего на {value}%. 😱 На моём стриме? И столь низкий процент?! Что ты скажешь в своё оправдание?!"
                if change == "+":
                    result += random.choice([" Ну хоть с прошлого раза побольше стало..", " Ну хоть с прошлого раза выросло.."])
                elif change == "-":
                    result += random.choice([" В прошлый раз побооольше было конечно.."])
        elif value < 40:
            if random.random() < 0.5:
                if change == "+":
                    result = f"@{user} хорни на {value}% и более того, я вижу как стремительно растёт твой... процент! >:з"
                elif change == "-":
                    result = f"@{user} хорни на {value}%, и я вижу как твой процент падает 😱 Надеюсь, что это временно, и в ближайшем будущем ты поднимешь свой... процент! >:з"
                else:
                    result = f"@{user} хорни на {value}% и я уповаю на то, что это временно, и в ближайшем будущем ты поднимешь свой... процент! >:з"
            else:
                if change == "+":
                    result = f"@{user} хорни на {value}%. Я вижу твои сомнения, но, кажется, ты двигаешься в нужную сторону >w>"
                else:
                    result = f"@{user} хорни на {value}%. Я вижу твои сомнения, но, надеюсь, в итоге ты выберешь тёмную сторону >w>"
        elif value < 60:
            if random.random() < 0.5:
                if change == "+":
                    result = f"@{user} хорни на {value}%. Золотая середина! А тот факт, что значение подросло с прошлого раза - не может не радовать!"
                else:
                    result = f"@{user} хорни на {value}%. Золотая середина, и это прекрасно!"
            else:
                result = f"@{user} хорни на {value}%. Отличный результат, но, думаю, нам всем стоит постараться и помочь @{user} поднять это значение до максимума!"
        elif value < 80:
            if random.random() < 0.5:
                result = f"В чатике становится жарко? Давайте подкинем дров и разгорим огонь сильнее, ведь @{user} хорни на {value}%"
                if change == "+":
                    result = f"@{user} хорни на {value}%. Твой процент такой.. большой! И с каждой минутой он становится всё больше и больше! Охх~ Становится действительно жарко~"
            else:
                result = f"Спрячьте весь Anti-Horny Spray™! Мы ведь не хотим лишиться разгорающихся страстей в чате, ибо у @{user} аж целых {value}%"
        elif value < 95:
            if random.random() < 0.5:
                result = f"Гляжу, под тобой уже мокро, ведь у тебя, @{user} - {value}%! Только не затопи чат, пожалуйста ;)"
                if random.random() < 0.25:
                    result += " Хотя кто знает, может они и не против ;)"
            else:
                result = f"Чат! Готовьтесь! У @{user} - {value}%, держите свои трусы, а то утащит!"
                # "Вот $randomfollowerusername уже без трусов, кто будет следующей жертвой?"
        else: # 95-100
            if random.random() < 0.5:
                result = f"Соседи снизу стучатся в дверь и ругаются, что их затопили. А всё потому что @{user} хорни на {value}%!"
            else:
                result = f"У @{user} - {value}%, кто же станет первой жертвой? Ставьте «+» в чат, если хотите попасть под раздачу!"
        return result

    async def _target_selected(self, user: str, targets: list[str]):
        variants = [
            f"{user} недостаточно своего хорни, поэтому трогает чужое ÒwÓ",
            f"{user} посылает свои хорни-вайбы в {join_targets(targets)}",
            f"{user} хочет захорнявить {join_targets(targets)} :>",
        ]
        return random.choice(variants)

    async def _handle_old(self, streamer: User, user: str, text: str, old_value: str, seconds_spend: str):
        value = old_value[1:] if old_value[0] in ["+", "-"] else old_value
        variants = [
            f"Мы уже узнали, что ты на {value}% хорни. Наберись терпения, мы уверены, что твой процент возрастёт :>",
            f"@{user} не терпится обновить свою хорнявность, в надежде, что она возрастёт, но нужно немного подождать :>",
        ]
        return random.choice(variants)


class BoopCommand(SimpleTargetCommand):
    command_name = "boop"
    command_aliases = ["boop", "буп", "бупнуть"]
    command_description = "Бупнуть пользователя чата в нос :з"

    need_target = True
    cooldown_timer = 60
    cooldown_count = 3

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_boop

    async def _handle(self, streamer: User, user: str, message: str, targets: list[str]) -> str:
        target = join_targets(targets)
        if len(targets) == 1 and random.random() < 0.1:
            return f"@{user} делает буп в нось {target}, но {target} внезапно чихает от этого. @{user}, кажется тебе стоит пойти помыть руку.."
        return f"@{user} делает буп в нось {target} !"

    async def _no_target_reply(self, user: str) -> str | None:
        return f"Чтобы бупнуть кого-нибудь в носярку, нужно указать, кого ты хочешь бупнуть! Например \"!буп @{user}\""

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return f"@{user}, подожди {delay_to_seconds(delay)}, прежде чем делать бупать снова :з"

    async def _self_call_reply(self, user: str) -> str | None:
        return random.choice([f"@{user} тыкает себя пальцем в нос", f"@{user} загадочно ощупывает свой нос о-о\""])

    async def _bot_call_reply(self, user: str, target: str) -> str | None:
        return random.choice([f"Прости, @{user}, но мы не можем бупнуть в нось бота. У ботов нет носов О:"])

    async def _this_bot_call_reply(self, user: str) -> str | None:
        return random.choice([f"*удивлённо скосил глаза и смотрит на свой нос*", f"{user} нось мой трогаешь? с: И как он тебе?"])

class CmdlistCommand(SimpleCDCommand):
    cooldown_timer_per_chat = 120
    cooldown_timer_per_user = 600

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        return f"Список команд в чате для этого бота: https://bot.quantum0.ru/cmdlist?streamer={streamer.login_name}"

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

    async def _handle(self, streamer: User, user: str, message: str, targets: list[str]) -> str:
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
        return f"@{user}, подожди, пожалуйста, {delay_to_seconds(delay)}, а то сейчас кому-нибудь лысину сделаешь своими поглаживаниями о:"

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

    async def _handle(self, streamer: User, user: str, message: str, targets: list[str]) -> str:
        target = join_targets(targets)
        join_to_hugs_str = ""
        if len(targets) == 1:
            assert isinstance(target, str)
            last_hug_target = await self._state_manager.get_state(channel=streamer.login_name, user=target[1:].lower(), command=self.command_name, param=SMParam.LAST_APPLY)
            if last_hug_target and time() - last_hug_target < 20:
                join_to_hugs_str = "присоединяется к обнимашкам и "
        variants = [
            f"@{user} {join_to_hugs_str}обнимает {target}",
            f"@{user} {join_to_hugs_str}крепко обнимает {target}",
            f"@{user} {join_to_hugs_str}набрасывается с объятиями на {target}",
            f"@{user} {join_to_hugs_str}стискивает в объятиях {target}",
            f"@{user} {join_to_hugs_str}заобнимовывает {target}",
        ]
        return random.choice(variants)

    async def _no_target_reply(self, user: str) -> str | None:
        return f"@{user} хочет обнимашек, но не справляется с выбором цели для этого, по-этому обнимает плюшевую акулку"

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return random.choice([f"@{user} подожди, секундочку, прежде чем обнимать кого-то другого!", f"@{user}, вы пока что в процессе обнимания другого пользователя! Подождите {delay_to_seconds(delay)}"])

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


class LurkCommand(SimpleCDCommand):
    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return None

    cooldown_timer_per_user = 30
    cooldown_timer_per_chat = None

    command_name = "lurk"
    command_aliases = ['lurk', 'unlurk', 'лурк', 'анлурк']
    command_description = "Сообщить стримеру и чатику, что вы уходите в лурк или возвращаетесь из него"

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_lurk

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        state: bool = not ('unlurk' in message or 'анлурк' in message)
        previous_state: bool = await self._state_manager.get_state(channel=streamer.login_name, user=user.lower(), command=self.command_name) is not None

        if state == previous_state and state is True:
            return f"@{user}, ты и так уже в лурке"

        if state and not previous_state:
            await self._state_manager.set_state(channel=streamer.login_name, user=user.lower(), command=self.command_name, value=time())
            variants = [
                f"@{user} прячется за холодильник и наблюдает за стримом оттуда. Спасибо за лурк!",
                f"@{user} спотыкается об камушек, падает и проваливается в лурк",
                f"У @{user} появились более важные дела, чем просмотр этого стрима, представляете?!",
                f"@{user} превращается в крокодила, погружается в ближайшую лужу, и теперь оттуда торчат только глазки 👀",
            ]
            return random.choice(variants)

        if previous_state and not state:
            await self._state_manager.set_state(channel=streamer.login_name, user=user.lower(), command=self.command_name, value=None)
            return f"@{user} выпылывает из лурка. С возвращением!"


class PantsCommand(SimpleCDCommand):
    command_name = "трусы"
    command_aliases = ['трусы', 'pants']
    command_description = "Запустить розыгрыш трусов"

    cooldown_timer_per_chat = 120
    cooldown_timer_per_user = 300
    cooldown_timer_per_target = 600

    def __init__(self, sm: StateManager, send_message: Callable[..., Awaitable[None]]) -> None:
        from dependencies import get_chat_bot
        self.chat_bot = next(get_chat_bot())
        super().__init__(sm, send_message)

    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_pants

    async def _handle(self, streamer: User, user: str, message: str) -> str:
        # Проверка — идёт ли уже розыгрыш
        pants_user = await self._state_manager.get_state(channel=streamer.login_name, command=self.command_name, param=SMParam.USER)
        if pants_user:
            return f"Невозможно начать новый розыгрыш трусов, пока не разыграли трусы @{pants_user}"

        # Выбор цели
        target: str | None = None
        if message.startswith("!трусы @"):
            targets = extract_targets(message, streamer.login_name)  # TODO replace with display name
            if len(targets) > 1:
                return "Для розыгрыша трусов нужно выбрать только одну цель!"
            target = targets[0][1:]

        if not target:
            targets = [x for x,y in await self.chat_bot.get_last_active_users(streamer.login_name)]
            target = random.choice(targets)

        # Проверяем кулдаун для цели
        last_ts = await self._state_manager.get_state(channel=streamer.login_name, command=self.command_name, user=target, param=SMParam.TARGET_COOLDOWN)
        if last_ts and time() - last_ts < self.cooldown_timer_per_target:
            return f"Трусы @{target} уже недавно разыгрывались. Подождём немного!"

        return "WiP"
        # # Запускаем розыгрыш
        # await self._state_manager.set_state(channel, self.command_name, SMParam.USER, target)
        # await self._state_manager.set_state(channel, self.command_name, SMParam.PARTICIPANTS, set())
        # await self.send_response(chat=channel, message=f"Внимание, розыгрыш трусов @{target}! Ставьте '+' в чат!")
        #
        # # Запускаем асинхронный таймер
        # asyncio.create_task(self.finish_raffle(channel, target))

    async def _cooldown_reply(self, user: str, delay: int) -> str | None:
        return ""


