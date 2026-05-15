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
        self._id_pattern = re.compile("[0-9a-f]{24}")

    @staticmethod
    def is_id(self, id_: str) -> bool:
        return bool(self._id_pattern.fullmatch(id_))

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
        self,
        cli: MemealertsAsyncClient,
        username: str,
        amount: int,
    ) -> bool:
        """
        Поиск саппортера и выдача ему коинов.
        """
        username_clean = username.lower().strip()

        # Если указан айдишник - выдаём сразу по нему
        if self.is_id(username_clean):
            logger.info(f"Giving memecoins by user_id=`{username}`")
            await cli.give_bonus(UserID(username_clean), amount)
            return True

        # Пытаемся достать из БД закэшированного саппортера
        try:
            supporter_from_db = await self.search_supporter_from_db(username_clean)
        except Exception:
            logger.error("Database error during pre-search", exc_info=True)
            supporter_from_db = None
        if supporter_from_db:
            logger.info(f"Giving memecoins for supporter from DB cache by username=`{username_clean}`")
            await cli.give_bonus(UserID(supporter_from_db.id), amount)
            return True

        # Ищем пользователя среди активных саппортеров стримера через API
        user_in_supporters = await self.find_user_in_supporters(cli, username_clean)
        if user_in_supporters:
            logger.info(f"Giving memecoins for supporter by username=`{username_clean}`")
            await cli.give_bonus(user_in_supporters.supporter_id, amount)
            return True

        # Последний шанс: Глобальный поиск через API
        try:
            user_in_search: User | None = await cli.find_user(username_clean)
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
        # TODO: link and name both - 2 supporters???
        lookup = {}
        for user in users:
            if user.supporter_link:
                lookup[user.supporter_link.lower()] = user
            if user.supporter_name:
                lookup[user.supporter_name.lower()] = user

        target_user = lookup.get(username)
        if target_user:
            return target_user

        logger.info(f"Failed to search {username} in supporters")
        return None

    async def load_supporters(self, cli: MemealertsAsyncClient) -> list[Supporter]:
        limit = 100
        first_page = await cli.get_supporters(limit=limit, skip=0)
        total_count = first_page.total
        items = first_page.data

        if total_count > limit:
            remaining_skips = list(range(limit, total_count, limit))
            tasks = [cli.get_supporters(limit=limit, skip=skip) for skip in remaining_skips]
            results = await asyncio.gather(*tasks)

            for page in results:
                items.extend(page.data)

            if len(items) != total_count:
                logger.warning("Total count = %s, items = %s", total_count, len(items))
            logger.info(f"Loaded {len(items)} supporters ({len(results) + 1} pages)")
        else:
            logger.info(f"Loaded {len(items)} supporters (1 page)")

        asyncio.create_task(self._safe_save_supporters(items))

        return items

    async def _safe_save_supporters(self, items: list[Supporter]) -> None:
        """Вспомогательный метод для безопасной фоновой записи"""
        try:
            await self.save_all_supporters_into_db(items)
        except Exception:
            logger.error("Background error saving supporters to db", exc_info=True)

    async def search_supporter_from_db(
        self,
        username: str,
    ):
        """
        Поиск саппортера в бд
        """
        q = (
            sa.select(MemealertsSupporters)
            .where(
                sa.or_(
                    sa.func.lower(MemealertsSupporters.name) == username,
                    sa.func.lower(MemealertsSupporters.link) == username
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
        if not supporters:
            return

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
