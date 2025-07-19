from datetime import datetime, UTC

from memealerts import MemealertsAsyncClient

import requests, re
from memealerts.types.models import User, Supporter
from memealerts.types.user_id import UserID


def token_expires_in_days(memealerts_token) -> int:
    expires_in: datetime = MemealertsAsyncClient(memealerts_token).token_expires_in
    return int((expires_in - datetime.now(UTC)).days)


async def load_supporters(cli: MemealertsAsyncClient) -> list[Supporter]:
    skip = 0
    limit = 100
    req = await cli.get_supporters(limit, skip=0)
    total_count = req.total
    items = req.data
    while len(items) < total_count:
        skip += limit
        req = await cli.get_supporters(limit, skip=skip)
        items += req.data
    assert len(items) == total_count
    return items


async def find_user(cli: MemealertsAsyncClient, username: str) -> User:
    return


def is_id(id_: str) -> bool:
    return bool(re.fullmatch("[0-9a-f]{24}", id_))


async def find_user_in_supporters(cli: MemealertsAsyncClient, username: str) -> Supporter | None:
    users = await load_supporters(cli)
    username = username.lower().strip()
    for user in users:
        if user.supporter_link.lower() == username or user.supporter_name.lower() == username:
            return user
    # print("Не удалось найти пользователя среди саппортеров")
    return None


async def find_and_give_bonus(cli: MemealertsAsyncClient, username: str, amount: int = 2) -> bool:
    if is_id(username):
        # print(f"Начисление мемкоинов по user_id=`{username}`")
        return bool(await cli.give_bonus(UserID(username), amount))

    user_in_supporters = await find_user_in_supporters(cli, username)
    if user_in_supporters:
        # print(f"Начисление мемкоинов саппортеру по username=`{username}`")
        return bool(await cli.give_bonus(user_in_supporters.supporter_id, amount))

    user_in_search = await cli.find_user(username)
    if user_in_search:
        # print(f"Начисление мемкоинов через общий поиск по username=`{username}`")
        return bool(await cli.give_bonus(user_in_search.id, amount))

    # print(f"Не удалось начислить мемкоины :с")
    return False


async def give_bonus(memealerts_token, streamer, supporter):
    meme_cli = MemealertsAsyncClient(memealerts_token)
    result = await find_and_give_bonus(meme_cli, supporter)
    # return "Мемкоины начислены :з" if result else "Ошибка начисления >.<"
    return result
