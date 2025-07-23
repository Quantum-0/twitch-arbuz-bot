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
        await chatbot.send_message(channel, f"Чтобы укусить кого-то, нужно указать, кого именно кусаешь. Например \"!Кусь @{message.user.display_name}\"")
        return
    await chatbot.send_message(channel, f"@{message.user.display_name} кусает {target} за пэрсики")
