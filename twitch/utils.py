import logging
from collections.abc import Callable
from typing import Awaitable


logger = logging.getLogger(__name__)


async def extract_targets(text: str, streamer_name: str, func_get_random_user: Callable[..., Awaitable[str]]) -> list[str]:
    # m = re.match("!\\w+ @.*[ $]", text)
    # if m:
    #     return m.lastgroup
    # else:
    #     return None
    streamer_alias = {
        "стримера", "стримлера", "стримеру", "стример", "стримлера",
        "стримлеру", "стримлера", "стримлер", "стримерша", "стримершу",
        "стримерши", "стримерка", "стримерку", "стримлерка", "стримлерша",
    }
    random_user = {
        "когото", "кого-то", "кого-нибудь", "когонибудь",
        "когонибуть", "кавота", "каво-та", "каво-то",
        "каво-нибудь", "рандом", "рандома", "рандому",
    }
    command, *other = text.split()
    other = [
        o
        for o in other
        if (o.startswith("@") and len(o) > 1)
        or o.lower()
        in [
            "все",
            "всех",
            "all",
            "всем",
            *list(random_user),
            "чат",
            "чатик",
            "чаттерсы",
            "чаттерсов",
            "чач",
            *list(streamer_alias),
        ]
    ]

    result = []
    for o in other:
        logger.info(f"Handle target `{o}`")
        if o in streamer_alias:
            o = "@" + streamer_name
        if o in random_user:
            o = await func_get_random_user()
            logger.info(f"`o` replaces to `{o}`")
        if o not in result:
            result.append(o)

    return result


def join_targets(targets) -> str | None:
    if not targets:
        return None
    elif len(targets) == 1:
        return targets[0]
    else:
        return ", ".join(targets[:-1]) + " и " + targets[-1]


def delay_to_seconds(delay: float | int) -> str:
    delay = int(delay)

    tens = delay // 10 % 10
    ones = delay % 10
    if ones == 1 and tens != 1:
        return f"{delay} секунду"
    if ones in (2, 3, 4) and tens != 1:
        return f"{delay} секунды"
    return f"{delay} секунд"
