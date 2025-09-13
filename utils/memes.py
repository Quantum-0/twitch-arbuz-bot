import asyncio
import logging.config
import re
from datetime import datetime, UTC

from memealerts import MemealertsAsyncClient
from memealerts.types.exceptions import MAUserNotFoundError, MATokenExpiredError
from memealerts.types.models import Supporter, User
from memealerts.types.user_id import UserID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import AsyncSessionLocal
from database.models import MemealertsSupporters
from utils.logging_conf import LOGGING_CONFIG

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


async def save_all_supporters_into_db(supporters: list[Supporter]) -> None:
    unique_data = {sup.supporter_id.root: sup for sup in supporters}.values()
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
    db: AsyncSession
    async with AsyncSessionLocal() as db:
        await db.execute(q)
        await db.commit()


async def token_expires_in_days(memealerts_token) -> int:
    try:
        async with MemealertsAsyncClient(memealerts_token) as cli:
            expires_in: datetime = cli.token_expires_in
            return int((expires_in - datetime.now(UTC)).days)
    except MATokenExpiredError:
        return 0


async def load_supporters(cli: MemealertsAsyncClient) -> list[Supporter]:
    limit = 100
    first_page = await cli.get_supporters(limit=limit, skip=0)
    total_count = first_page.total
    items = first_page.data

    if total_count <= limit:
        await save_all_supporters_into_db(items)
        logger.info(f"Loaded {len(items)} supporters (1 page)")
        return items

    # Подготовка остальных запросов
    remaining_skips = list(range(limit, total_count, limit))
    tasks = [
        cli.get_supporters(limit=limit, skip=skip)
        for skip in remaining_skips
    ]
    results = await asyncio.gather(*tasks)

    # Сборка всех данных
    for page in results:
        items.extend(page.data)

    assert len(items) == total_count
    logger.info(f"Loaded {len(items)} supporters ({len(results) + 1} pages)")
    await save_all_supporters_into_db(items)
    return items


def is_id(id_: str) -> bool:
    return bool(re.fullmatch("[0-9a-f]{24}", id_))


async def find_user_in_supporters(cli: MemealertsAsyncClient, username: str) -> Supporter | None:
    users = await load_supporters(cli)
    username = username.lower().strip()
    # TODO: link and name both - 2 supporters???
    for user in users:
        if (user.supporter_link and user.supporter_link.lower() == username) or user.supporter_name.lower() == username:
            return user
    logger.info(f"Failed to search {username} in supporters")
    return None


async def find_and_give_bonus(cli: MemealertsAsyncClient, username: str, amount: int = 2) -> bool:
    if is_id(username):
        logger.info(f"Giving memecoins by user_id=`{username}`")
        await cli.give_bonus(UserID(username), amount)
        return True

    user_in_supporters = await find_user_in_supporters(cli, username)
    if user_in_supporters:
        # print(f"Начисление мемкоинов саппортеру по username=`{username}`")
        logger.info(f"Giving memecoins for supporter by username=`{username}`")
        await cli.give_bonus(user_in_supporters.supporter_id, amount)
        return True

    # TODO: search in database supporters

    try:
        user_in_search: User | None = await cli.find_user(username)
    except MAUserNotFoundError:
        user_in_search = None
    if user_in_search:
        logger.info(f"Giving memecoins via global search by username=`{username}`")
        await cli.give_bonus(user_in_search.id, amount)
        return True

    logger.info(f"Failed to give bonus")
    return False


async def give_bonus(memealerts_token, streamer, supporter, amount):
    logger.info(f"Giving bonus {amount} memecoins from {streamer} to {supporter}")
    async with MemealertsAsyncClient(memealerts_token) as meme_cli:
        result = await find_and_give_bonus(meme_cli, supporter, amount)
        return result
