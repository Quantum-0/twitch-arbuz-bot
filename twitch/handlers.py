import random
import re
from typing import TYPE_CHECKING

from twitchAPI.chat import ChatMessage, ChatUser

if TYPE_CHECKING:
    from twitch.bot import ChatBot


def extract_targets(text: str) -> str | None:
    # m = re.match("!\\w+ @.*[ $]", text)
    # if m:
    #     return m.lastgroup
    # else:
    #     return None
    command, *other = text.split()
    other = [
        o for o
        in other
        if
        (o.startswith("@") and len(o) > 1)
        or
        o.lower() in [
            "все", "всех", "all", "всем", "кого-то", "стримера", "стримлера", "стример", "стримлер", "чат", "чаттерсы", "чаттерсов", "чач"
        ]
    ]

    if not other:
        return None
    elif len(other) == 1:
        return other[0]
    else:
        return ", ".join(other[:-1]) + " и " + other[-1]


async def cmd_bite_handler(chatbot: "ChatBot", channel: str, message: ChatMessage):
    target = extract_targets(message.text)
    if not target:
        await chatbot.send_message(channel, f"Чтобы укусить кого-то, нужно указать, кого именно кусаешь. Например \"!кусь @{message.user.display_name}\"")
        return
    kind_of_bite = ["злобный", "приятный", "мягкий", "нежный", "аккуратный", "агрессивный", "коварный"]
    target_to_bite = ["ухо", "пятку", "хвост", "ногу", "пэрсики", "нос", "плечо", "жёпку"]
    await chatbot.send_message(channel, f"@{message.user.display_name} делает {random.choice(kind_of_bite)} кусь {target} за {random.choice(target_to_bite)}")


async def cmd_lick_handler(chatbot: "ChatBot", channel: str, message: ChatMessage):
    target = extract_targets(message.text)
    if not target:
        await chatbot.send_message(channel, f"Чтобы кого-то лизнуть, нужно указать, кого именно ты хочешь лизнуть. Например \"!лизь @{message.user.display_name}\"")
        return
    user = message.user.display_name
    random_variants = [
        f'{user} вылизывает всё лицо {target}',
        f'{user} облизывает ухо {target}',
        f'{user} лижет в нос {target}',
        f'{user} пытается лизнуть {target}, но {target} успешно уворачивается от нападения языком!',
    ]
    await chatbot.send_message(channel, random.choice(random_variants))


async def cmd_boop_handler(chatbot: "ChatBot", channel: str, message: ChatMessage):
    target = extract_targets(message.text)
    if not target:
        await chatbot.send_message(channel, f"Чтобы кого-то лизнуть, нужно указать, кого именно ты хочешь лизнуть. Например \"!лизь @{message.user.display_name}\"")
        return
    user = message.user.display_name
    await chatbot.send_message(channel, f"{user} делает буп в нось {target} !")
