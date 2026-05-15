import asyncio
import logging
import re
from collections.abc import Callable

from memealerts import MemealertsAsyncClient
from memealerts.types.exceptions import MAUserNotFoundError
from memealerts.types.models import Supporter, User
from memealerts.types.user_id import UserID
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.models import MemealertsSupporters
import sqlalchemy as sa

logger = logging.getLogger(__name__)

class MemealertsService:
    def __init__(
        self,
        db_session_factory: Callable[[], AsyncSession],
    ):
        self._db_session_factory = db_session_factory

    @staticmethod
    def is_id(id_: str) -> bool:
        return bool(re.fullmatch("[0-9a-f]{24}", id_))

    async def give_bonus(self, memealerts_token, streamer, supporter, amount):
        """
        Внешний метод начисления бонуса саппортеру от стримера
        Инитим тут клиент и выполняем с ним поиск и выдачу коинов
        """
        logger.info(f"Giving bonus {amount} memecoins from {streamer} to {supporter}")
        async with MemealertsAsyncClient(memealerts_token) as meme_cli:
            result = await self.find_and_give_bonus(meme_cli, supporter, amount)
            return result

    async def find_and_give_bonus(
        self, cli: MemealertsAsyncClient, username: str, amount: int = 2, *, db_session_factory,
    ) -> bool:
        """
        Поиск саппортера и выдача ему коинов.
        """
        # Если указан айдишник - выдаём сразу по нему
        if self.is_id(username):
            logger.info(f"Giving memecoins by user_id=`{username}`")
            await cli.give_bonus(UserID(username), amount)
            return True

        # Пытаемся найти пользователя среди саппортеров
        user_in_supporters = await self.find_user_in_supporters(cli, username)
        if user_in_supporters:
            logger.info(f"Giving memecoins for supporter by username=`{username}`")
            await cli.give_bonus(user_in_supporters.supporter_id, amount)
            return True

        # Пытаемся достать из БД закэшированного саппортера
        try:
            supporter_from_db = await self.search_supporter_from_db(username)
        except:
            supporter_from_db = None
        if supporter_from_db:
            logger.info(f"Giving memecoins for supporter by username=`{username}`")
            await cli.give_bonus(UserID(supporter_from_db.id), amount)
            return True

        # Пытаемся найти пользователя через глобальный поиск
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

    async def find_user_in_supporters(
        self,
        cli: MemealertsAsyncClient,
        username: str,
    ) -> Supporter | None:
        logger.debug("Search from supporter in all streamer supporters")
        users = await self.load_supporters(cli)
        username = username.lower().strip()
        # TODO: link and name both - 2 supporters???
        for user in users:
            if (
                (user.supporter_link and user.supporter_link.lower() == username)
                or
                user.supporter_name.lower() == username
            ):
                return user
        logger.info(f"Failed to search {username} in supporters")
        return None

    async def load_supporters(self, cli: MemealertsAsyncClient) -> list[Supporter]:
        limit = 100
        first_page = await cli.get_supporters(limit=limit, skip=0)
        total_count = first_page.total
        items = first_page.data

        if total_count <= limit:
            try:
                await self.save_all_supporters_into_db(items)
            except:
                logger.error("Error saving supporters to db", exc_info=True)
            logger.info(f"Loaded {len(items)} supporters (1 page)")
            return items

        # Подготовка остальных запросов
        remaining_skips = list(range(limit, total_count, limit))
        tasks = [cli.get_supporters(limit=limit, skip=skip) for skip in remaining_skips]
        results = await asyncio.gather(*tasks)

        # Сборка всех данных
        for page in results:
            items.extend(page.data)

        if len(items) == total_count:
            logger.warning("Total count = %s, items = %s", total_count, len(items))
        logger.info(f"Loaded {len(items)} supporters ({len(results) + 1} pages)")

        try:
            await self.save_all_supporters_into_db(items)
        except:
            logger.error("Error saving supporters to db", exc_info=True)

        return items

    async def search_supporter_from_db(
        self,
        username: str,
    ):
        q = (
            sa.select(MemealertsSupporters)
            .where(
                sa.or_(
                    MemealertsSupporters.name == username,
                    MemealertsSupporters.link == username
                )
            )
        )
        db: AsyncSession
        async with self._db_session_factory() as db:
            result: MemealertsSupporters | None = (await db.execute(q)).scalar_one_or_none()
            return result

    async def save_all_supporters_into_db(
        self,
        supporters: list[Supporter],
    ) -> None:
        unique_data = {sup.supporter_id.root: sup for sup in supporters}.values()
        data = [
            {
                "id": sup.supporter_id.root,
                "link": sup.supporter_link or sup.supporter_name,
                "name": sup.supporter_name,
            }
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
        async with self._db_session_factory() as db:
            await db.execute(q)
            await db.commit()
