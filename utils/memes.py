from datetime import datetime, UTC
import re

from fastapi.params import Depends
from memealerts import MemealertsAsyncClient
from memealerts.types.models import Supporter, User
from memealerts.types.user_id import UserID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import AsyncSessionLocal
from database.models import MemealertsSupporters
from dependencies import get_db


async def save_all_supporters_into_db(supporters: list[Supporter]) -> None:
    unique_data = {sup.supporter_id: sup for sup in supporters}.values()
    data = [
        {"id": sup.supporter_id.root, "link": sup.supporter_link, "name": sup.supporter_name}
        for sup in unique_data
    ]
    q = pg_insert(MemealertsSupporters).values(data)
    q = q.on_conflict_do_update(
        index_elements=(MemealertsSupporters.id,),
        set_={
            "link": q.excluded.link,
            "name": q.excluded.name,
        },
    )
    async with AsyncSessionLocal() as db:
        await db.execute(q)


async def token_expires_in_days(memealerts_token) -> int:
    async with MemealertsAsyncClient(memealerts_token) as cli:
        expires_in: datetime = cli.token_expires_in
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
    await save_all_supporters_into_db(items)
    return items


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

    # TODO: search in database supporters

    user_in_supporters = await find_user_in_supporters(cli, username)
    if user_in_supporters:
        # print(f"Начисление мемкоинов саппортеру по username=`{username}`")
        return bool(await cli.give_bonus(user_in_supporters.supporter_id, amount))

    user_in_search: User = await cli.find_user(username)
    if user_in_search:
        # print(f"Начисление мемкоинов через общий поиск по username=`{username}`")
        return bool(await cli.give_bonus(user_in_search.id, amount))

    # print(f"Не удалось начислить мемкоины :с")
    return False


async def give_bonus(memealerts_token, streamer, supporter, amount):
    async with MemealertsAsyncClient(memealerts_token) as meme_cli:
        result = await find_and_give_bonus(meme_cli, supporter, amount)
        return result
