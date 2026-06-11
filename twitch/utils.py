import logging
from collections.abc import Callable
from typing import Awaitable


logger = logging.getLogger(__name__)


async def extract_targets(
    text: str,
    streamer_name: str,
    self_name: str,
    func_get_random_user: Callable[..., Awaitable[str]],
    func_get_active_users: Callable[..., list[tuple[str, float]]],
) -> list[str]:
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
    self_alias = {
        "себя", "я", "себе", "меня", "мне", "мои",
    }
    random_user = {
        "когото", "кого-то", "кого-нибудь", "когонибудь",
        "когонибуть", "кавота", "каво-та", "каво-то",
        "каво-нибудь", "рандом", "рандома", "рандому",
    }
    all_users = {
        "все", "всех", "all", "всем",
        "чат", "чач", "чатик",
        "чаттерсы", "чатерсов", "чатерсы", "чаттерсов", "чаттерсам", "чатерсам",
        "зрителей", "зрители",
    }
    command, *other = text.split()
    other = [
        o
        for o in other
        if (o.startswith("@") and len(o) > 1)
        or o.lower()
        in [
            *list(all_users),
            *list(random_user),
            *list(streamer_alias),
            *list(self_alias),
        ]
    ]

    result = []
    for o in other:
        logger.info(f"Handle target `{o}`")
        if o in streamer_alias:
            o = "@" + streamer_name
            logger.debug(f"`o` replaces to `{o}`")
        if o in self_alias:
            o = "@" + self_name
            logger.debug(f"`o` replaces to `{o}`")
        if o in random_user:
            o = "@" + await func_get_random_user()
            logger.debug(f"`o` replaces to `{o}`")
        if o in all_users:
            all_users = func_get_active_users(timeout=30*60)  # берём последних за пол часа
            if len(all_users) < 7:
                result.extend("@" + usr[0] for usr in all_users)
                continue
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
