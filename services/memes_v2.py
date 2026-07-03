import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Generic, TypeVar

from sqlalchemy.dialects.postgresql import insert as pg_insert
import httpx
import sqlalchemy as sa
from memealerts.types.exceptions import MATokenExpiredError, MAUserNotFoundError, MAError
from memealerts.types.user_id import UserID
from opentelemetry import trace
from pydantic import BaseModel, PrivateAttr, ValidationError, computed_field, model_validator
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from config import memealerts_scope, settings
from database.models import MemealertsSettings, User, MemealertsSupporters
from exceptions import MARefreshTokenError, MAInvalidTokenError, MAUnavailableError, MAValidationRespError, MANoToken, \
    MAInvalidScopeError, MADuplicateUserError
from schemas.api import BoolResponseSchema
from schemas.memealerts import MAUserInfo, MASupportersList, MASupporter

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

T = TypeVar("T", bound=str)


class Tokens(BaseModel, Generic[T]):
    access_token: T
    refresh_token: T
    # _access_token: T = Field(alias="access_token", repr=False)
    # _refresh_token: T = Field(alias="access_token", repr=False)
    _expires_in: float = PrivateAttr(default=60 * 60 - 1)
    _expires_at: datetime
    _refresh_expires_at: datetime
    _refresh_expires_in: float = PrivateAttr(default=30 * 24 * 60 * 60 - 1)
    # _raw_access_token: T = PrivateAttr()
    # _raw_refresh_token: T = PrivateAttr()
    # TODO: Error when try to call token.access_token when expired

    # @computed_field
    # @property
    # def access_token(self) -> T:
    #     if self.is_expired:
    #         raise MATokenExpiredError()
    #     return self._raw_access_token
    #
    # @computed_field
    # @property
    # def refresh_token(self) -> T:
    #     if self.is_refresh_expired:
    #         raise MATokenExpiredError()
    #     return self._raw_refresh_token

    @model_validator(mode="after")
    def calc_expiration(self):
        now = datetime.now(UTC)
        extra_data = self.__pydantic_extra__ or {}
        expires_in = extra_data.pop("expires_in", None)
        expires_at = extra_data.pop("expires_at", None)
        if expires_in is None and expires_at is None:
            raise ValueError("Missing `expires_in`")
        if expires_in is not None:
            expires_at = now + timedelta(seconds=(expires_in - 1))
            refresh_expires_at = now + timedelta(seconds=(self._refresh_expires_in - 1))
            self._expires_at = expires_at.replace(tzinfo=None)
            self._refresh_expires_at = refresh_expires_at.replace(tzinfo=None)
        else:
            self._expires_at = expires_at
            self._refresh_expires_at = expires_at + timedelta(days=30, hours=-1)

        # self._raw_access_token = self.access_token
        # self._raw_refresh_token = self.refresh_token
        return self

    @computed_field
    @property
    def expires_at(self) -> datetime:
        return self._expires_at

    @computed_field
    @property
    def expires_in(self) -> float:
        return max(0, (self.expires_at - datetime.now(UTC).replace(tzinfo=None)).total_seconds())

    @computed_field
    @property
    def refresh_expires_at(self) -> datetime:
        return self._refresh_expires_at

    @computed_field
    @property
    def refresh_expires_in(self) -> float:
        return max(0, (self.refresh_expires_at - datetime.now(UTC).replace(tzinfo=None)).total_seconds())

    @computed_field
    @property
    def is_expired(self) -> bool:
        return self.expires_in == 0

    @computed_field
    @property
    def is_refresh_expired(self) -> bool:
        return self.refresh_expires_in == 0

    model_config = {"extra": "allow", "populate_by_name": True}


class MemealertsOAuthService:
    def __init__(
        self,
        db_session_factory: Callable[[], AsyncSession],
    ):
        self._db_session_factory = db_session_factory
        self._refresh_semaphore = asyncio.Semaphore(10)

    async def auth_user(self, authorization_code: str, user: User) -> Tokens[str] | None:
        """
        Процесс авторизации пользователя
        принимаем самого пользователя и код авторизации
        сохраняем токены в бд
        возвращаем токены в ответ
        """
        tokens = await self._request_tokens(authorization_code=authorization_code)
        if tokens:
            await self._save_token(tokens=tokens, user_id=user.id)
        return tokens

    async def run_periodic_update(self) -> None:
        """
        Запуск переодической таски
        Таска выгружает из бд все токены,
        которым до окончания действия рефреш токена
        осталось ~5 дней
        После этого таска пытается их обновить
        через Memealerts
        а затем сохраняет обновлённые токены обратно в бд.
        """
        logger.info("Run updating memealerts tokens")
        q = sa.select(MemealertsSettings).where(
            MemealertsSettings.token_expires_at + sa.text("INTERVAL '25 days'") > sa.func.now()
        )
        async with self._db_session_factory() as db:
            memealerts_settings: Sequence[MemealertsSettings] = (await db.execute(q)).scalars().all()

        users_list = [mas.user_id for mas in memealerts_settings]
        tokens_list = [self._tokens_from_settings(mas) for mas in memealerts_settings]

        new_tokens_list = await asyncio.gather(
            *(self._refresh_tokens_if_need(t) for t in tokens_list), return_exceptions=True
        )

        success_updates: list[tuple[int, Tokens]] = []

        for user, old_tokens, new_tokens_result in zip(users_list, tokens_list, new_tokens_list, strict=True):
            if isinstance(new_tokens_result, Exception):
                logger.error(f"Не удалось обновить токен для {user}: {new_tokens_result}", exc_info=new_tokens_result)
            elif old_tokens == new_tokens_result:
                logger.debug(f"Токен для {user} не обновлён, т.к. не был изменён")
            else:
                success_updates.append((user, new_tokens_result))

        if success_updates:
            async with self._db_session_factory() as db:
                for user, new_token in success_updates:
                    await self._save_token(new_token, user, session=db)
                await db.commit()
                logger.info(f"Успешно обновлено токенов в БД: {len(success_updates)}")

    async def get_token_of_user(self, user: User) -> Tokens[str]:
        """
        Возвращает всегда актуальный токен пользователя
        :param user: пользователь в БД
        :return: актуальный токен из мемалёртса
        """
        if user.memealerts.access_token is None:
            raise MANoToken
        if any(item not in user.memealerts.token_scopes for item in memealerts_scope):
            raise MAInvalidScopeError
        if isinstance(user, User):
            current_token = self._tokens_from_settings(user.memealerts)
        else:
            raise NotImplementedError
        new_tokens = await self._refresh_tokens_if_need(tokens=current_token)
        if new_tokens != current_token:
            await self._save_token(new_tokens, user.id)
        return new_tokens

    @staticmethod
    def _tokens_from_settings(ma_settings: MemealertsSettings) -> Tokens[str]:
        """
        Конвертация настроек мемалёртса в токен
        """
        return Tokens(
            access_token=ma_settings.access_token,
            refresh_token=ma_settings.refresh_token,
            expires_at=ma_settings.token_expires_at,  # type: ignore
            _refresh_expires_at=ma_settings.token_refresh_expires_at
        )

    async def _save_token(self, tokens: Tokens[str], user_id: int, session: AsyncSession | None = None):
        """
        Сохранение нового токена в бд.
        """
        if session is not None:
            await session.execute(
                sa.update(MemealertsSettings)
                .where(MemealertsSettings.user_id == user_id)
                .values(
                    access_token=tokens.access_token,
                    refresh_token=tokens.refresh_token,
                    token_expires_at=tokens.expires_at,
                    token_refresh_expires_at=tokens.refresh_expires_at,
                    token_created_at=sa.func.now(),
                    token_scopes=" ".join(memealerts_scope),
                )
            )
        else:
            async with self._db_session_factory() as db:
                await db.execute(
                    sa.update(MemealertsSettings)
                    .where(MemealertsSettings.user_id == user_id)
                    .values(
                        access_token=tokens.access_token,
                        refresh_token=tokens.refresh_token,
                        token_expires_at=tokens.expires_at,
                        token_refresh_expires_at=tokens.refresh_expires_at,
                        token_created_at=sa.func.now(),
                        token_scopes=" ".join(memealerts_scope),
                    )
                )
                await db.commit()

    async def _refresh_tokens_if_need(self, tokens: Tokens[str]) -> Tokens[str]:
        """
        Обновляет токен если в этом есть необходимость:
        - Если рефреш токен истёк - отдаём ошибку
        - Если рефреш токен не истёк, и время жизни аксес токена ещё больше 10 минут
        -- отдаём существующий токен
        - Если время жизни аксес токена больше минуты
        -- пробуем обновить, если не выходит - отдаём старый токен
        - Если время жизни токена меньше минуты
        -- отдаём всегда новый
        -- если не можем обновить - отдаём ошибку

        Таким образом у нас всегда будет валидный аксес токен, с временем жизни ещё минимум минута
        """
        if tokens.is_refresh_expired:
            logger.error("Cannot refresh token after because refresh token is already expired")
            raise MATokenExpiredError
        if tokens.expires_in <= 60:
            last_error = None
            for _ in range(5):
                try:
                    return await self._request_tokens(refresh_token=tokens.refresh_token)
                except httpx.HTTPError as exc:
                    last_error = exc
                    await asyncio.sleep(5)
            assert last_error is not None  # noqa S101
            raise last_error
        if 60 < tokens.expires_in < 600:
            try:
                return await self._request_tokens(refresh_token=tokens.refresh_token)
            except httpx.HTTPError:
                return tokens
        # if tokens.expires_in >= 600:
        return tokens

    async def _request_tokens(self, *, authorization_code: str = None, refresh_token: str = None) -> Tokens[str]:
        """
        Этап oAuth процесса:
        Обмен кода авторизации или старого рефреш токена
        на access_token & refresh_token
        :param authorization_code: токен авторизации, полученный через redirect_url
        :param refresh_token: старый refresh_token
        :return: access_token, refresh_token & expires_at.
        """
        if authorization_code is not None and refresh_token is not None:
            raise ValueError("Only one argument should be passed.")
        if authorization_code is None and refresh_token is None:
            raise ValueError("One of arguments should be passed.")

        grant_type = "refresh_token" if refresh_token else "authorization_code"
        data = {
            "client_id": settings.memealerts_client_id.get_secret_value(),
            "client_secret": settings.memealerts_client_secret.get_secret_value(),
            "refresh_token": refresh_token,
            "grant_type": grant_type,
            "code": authorization_code,
        }
        if authorization_code:
            data["redirect_uri"] = settings.memealerts_redirect_url
        async with self._refresh_semaphore, httpx.AsyncClient() as client:
            response = await client.post(
                "https://memealerts.com/oauth/token",
                timeout=5 if refresh_token else 10,
                data=data,
            )
            response.raise_for_status()
            tokens = response.json()
            try:
                return Tokens(**tokens)
            except ValidationError as exc:
                logger.error(f"Error getting tokens from oauth. Resp: {tokens}")
                raise MARefreshTokenError from exc

    async def delete_token(self, user: User):
        async with self._db_session_factory() as db:
            await db.execute(
                sa.update(MemealertsSettings)
                .where(MemealertsSettings.user_id == user.id)
                .values(
                    access_token=None,
                    refresh_token=None,
                    token_expires_at=None,
                    token_refresh_expires_at=None,
                    token_created_at=None,
                    token_scopes=None,
                )
            )
            await db.commit()


class MemealertsV2Service:
    def __init__(
        self,
        db_session_factory: Callable[[], AsyncSession],
    ):
        self._db_session_factory = db_session_factory
        self._api_semaphore = asyncio.Semaphore(50)
        self._id_pattern = re.compile("[0-9a-f]{24}")
        # Защита фоновых задач от сборщика мусора (GC)
        self._background_tasks: set[asyncio.Task] = set()

    def is_id(self, id_: str) -> bool:
        return bool(self._id_pattern.fullmatch(id_))

    async def get_user_info(self, ma_token: Tokens[str]) -> MAUserInfo:
        """
        Получение информации о текущем пользователе
        :param ma_token: OAuth-токен Memealerts
        :return: Информация о пользователе.
        """
        async with self._api_semaphore, httpx.AsyncClient() as client:
            response = await client.get(
                "https://memealerts.com/api/v1/user/oauth",
                timeout=10,
                headers={"Authorization": f"Bearer {ma_token.access_token}"}
            )
            if response.status_code in {500, 502}:
                raise MAUnavailableError
            if response.status_code != 200:
                logger.error(f"Access error from MA. Resp: {response.json()}")
                raise MAInvalidTokenError
            data = response.json()["data"]
            try:
                return MAUserInfo(**data)
            except ValidationError as exc:
                raise MAValidationRespError from exc

    @tracer.start_as_current_span("MAv2: Give bonus")
    async def give_bonus(self, ma_token: Tokens[str], streamer: str, supporter: str, amount: int):
        """
        Внешний метод начисления бонуса саппортеру от стримера
        Инитим тут клиент и выполняем с ним поиск и выдачу коинов
        """
        logger.info(f"Giving bonus {amount} memecoins from {streamer} to {supporter}")
        current_span = trace.get_current_span()
        if current_span.is_recording():
            current_span.set_attribute("ma.streamer", streamer)
            current_span.set_attribute("ma.supporter", supporter)
            current_span.set_attribute("ma.amount", amount)

        try:
            result: bool = await self.find_and_give_bonus(ma_token, supporter, amount)
        except MAError as exc:
            logger.warning(f"Error! Failed to give bonus to `{supporter}` form `{streamer}. Error {exc}")
            raise exc

        if current_span.is_recording():
            current_span.set_attribute("ma.success", result)
        return result

    async def find_and_give_bonus(
        self,
        ma_token: Tokens[str],
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
            await self._give_bonus(ma_token=ma_token, user_id=UserID(username_clean), value=amount)
            return True

        # Пытаемся достать из БД закэшированного саппортера
        try:
            supporter_from_db = await self.search_supporter_from_db(username_clean)
        except MultipleResultsFound as exc:
            logger.warning(f"Found multiple rows in database with username=`{username_clean}`")
            supporter_from_db = None
        except Exception:
            logger.error("Database error during pre-search", exc_info=True)
            supporter_from_db = None

        if supporter_from_db:
            logger.info(f"Giving memecoins for supporter from DB cache by username=`{username_clean}`")
            await self._give_bonus(ma_token=ma_token, user_id=UserID(supporter_from_db.id), value=amount)
            return True

        # Ищем пользователя среди саппортеров стримера через API (сначала точечно, затем полностью)
        user_in_supporters = await self.find_user_in_supporters(ma_token, username_clean)

        if user_in_supporters:
            logger.info(f"Giving memecoins for supporter by username=`{username_clean}`")
            await self._give_bonus(user_in_supporters.supporter_id, amount)
            return True

        # Последний шанс: Глобальный поиск через API
        # api_global_start = time.perf_counter()
        # try:
        #     user_in_search: User | None = await cli.find_user(username_clean)
        #     logger.debug(f"API global search took {time.perf_counter() - api_global_start:.4f}s")
        # except MAUserNotFoundError:
        #     user_in_search = None
        # if user_in_search:
        #     logger.info(f"Giving memecoins via global search by username=`{username}`")
        #     await cli.give_bonus(user_in_search.id, amount)
        #     return True

        logger.info(f"Failed to give bonus")
        return False

    async def _give_bonus(self, ma_token: Tokens[str], user_id: UserID, value: int) -> bool:
        """
        Выдача бонуса пользователю по ID.
        :param ma_token: OAuth-токен Memealerts;
        :param user_id: ID-пользователя получателя коинов;
        :param value: Количество коинов, выдаваемых пользователю;
        :return: Успех выполнения (скорее всего всегда true или ошибка).
        """
        if not (0 < value <= 1000):
            raise ValueError("Value should be between 0 and 1000")
        async with self._api_semaphore, httpx.AsyncClient() as client:
            response = await client.post(
                "https://memealerts.com/api/v1/user/give-bonus",
                timeout=10,
                headers={"Authorization": f"Bearer {ma_token.access_token}"},
                params={
                    "userId": str(user_id),
                    "value": value,
                }
            )
            if response.status_code in {500, 502}:
                raise MAUnavailableError
            # TODO: Если коллеги из MA согласятся на использование 404 - тут разделить логику
            #  - получаем 404 - идём (извне) запрашиваем, включён ли приветственный бонус
            #  - включён - просим пользака его забрать, выключен - просим его купить коины или кинуть донат
            if response.status_code == 404:
                raise MAUserNotFoundError
            if response.status_code in {401, 403}:
                logger.error(f"Access error from MA. Resp: {response.json()}")
                raise MAInvalidTokenError
            data = response.json()
            try:
                return BoolResponseSchema(**data).result
            except ValidationError as exc:
                raise MAValidationRespError from exc

    async def _get_supporters(self, ma_token: Tokens[str], skip: int | None = None, limit: int | None = None, query: str | None = None) -> MASupportersList:
        params = {}
        if skip is not None:
            if skip < 0:
                raise ValueError("Value should be more than 0")
            params["skip"] = skip
        if limit is not None:
            if not (0 < limit <= 100):
                raise ValueError("Value should be between 0 and 100")
            params["limit"] = limit
        if query:
            params["query"] = query
        async with self._api_semaphore, httpx.AsyncClient() as client:
            response = await client.post(
                "https://memealerts.com/api/v1/user/supporters",
                timeout=10,
                headers={"Authorization": f"Bearer {ma_token.access_token}"},
                params=params,
            )
            if response.status_code in {500, 502}:
                raise MAUnavailableError
            if response.status_code in {401, 403}:
                logger.error(f"Access error from MA. Resp: {response.json()}")
                raise MAInvalidTokenError
            data = response.json()
            try:
                return MASupportersList(**data)
            except ValidationError as exc:
                raise MAValidationRespError from exc

    async def load_supporters(self, ma_token: Tokens[str], query: str | None = None) -> list[MASupporter]:
        limit = 100
        first_page = await self._get_supporters(ma_token=ma_token, limit=limit, skip=0, query=query)
        total_count = first_page.total
        items = first_page.data

        if total_count > limit:
            remaining_skips = list(range(limit, total_count, limit))
            tasks = [self._get_supporters(ma_token=ma_token, limit=limit, skip=skip, query=query) for skip in remaining_skips]
            results = await asyncio.gather(*tasks)

            for page in results:
                items.extend(page.data)

            if len(items) != total_count:
                logger.warning("Total count = %s, items = %s", total_count, len(items))

        logger.info(f"Loaded {len(items)} supporters for query='{query}' ({total_count} total)")

        # Безопасный запуск фоновой задачи с сохранением сильной ссылки
        task = asyncio.create_task(self._safe_save_supporters(items))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return items

    @tracer.start_as_current_span("MA: Find user in supporters")
    async def find_user_in_supporters(
        self,
        ma_token: Tokens[str],
        username: str,
    ) -> MASupporter | None:
        # ШАГ А: Точечный поиск по подстроке через API саппортеров
        logger.debug(f"Targeted search from supporters with query=`{username}`")
        with tracer.start_as_current_span("Search by query"):
            try:
                targeted_users = await self.load_supporters(ma_token, query=username)
                target_user = self._pick_user_from_list(targeted_users, username)
            except:
                target_user = None

        if target_user:
            return target_user

        # ШАГ Б: Фолбэк на полный скан, если по query ничего не нашлось
        logger.warning(f"Targeted search failed for `{username}`. Falling back to full scan.")
        with tracer.start_as_current_span("Full list search"):
            all_users = await self.load_supporters(ma_token, query=None)
            target_user = self._pick_user_from_list(all_users, username)
        return target_user

    def _pick_user_from_list(self, users: list[MASupporter], username: str) -> MASupporter | None:
        """Оптимизированный поиск совпадения в списке с защитой от дубликатов"""
        lookup = {}
        target_user = None

        for user in users:
            # 1. Проверяем supporter_link
            if user.supporter_link:
                link_clean = user.supporter_link.lower()
                if link_clean not in lookup:
                    lookup[link_clean] = user
                    if link_clean == username:
                        target_user = user
                else:
                    logger.warning(f"Duplicate memealerts link=`{link_clean}`")
                    if link_clean == username:
                        raise MADuplicateUserError(username)

            # 2. Проверяем supporter_name
            if user.supporter_name:
                name_clean = user.supporter_name.lower()
                if name_clean not in lookup:
                    lookup[name_clean] = user
                    if name_clean == username:
                        target_user = user
                else:
                    logger.warning(f"Duplicate memealerts name=`{name_clean}`")
                    if name_clean == username:
                        raise MADuplicateUserError(username)

        return target_user

    @tracer.start_as_current_span("Load supported from db cache")
    async def search_supporter_from_db(self, username: str) -> MemealertsSupporters | None:
        """
        Поиск саппортера в бд
        """
        q = sa.select(MemealertsSupporters).where(
            sa.or_(
                sa.func.lower(MemealertsSupporters.name) == username,
                sa.func.lower(MemealertsSupporters.link) == username,
            )
        )
        db: AsyncSession
        async with self._db_session_factory() as db:
            result: MemealertsSupporters | None = (await db.execute(q)).scalar_one_or_none()
            return result

    async def save_all_supporters_into_db(self, supporters: list[MASupporter]) -> None:
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

    @tracer.start_as_current_span("Saving supporters list to DB")
    async def _safe_save_supporters(self, items: list[MASupporter]) -> None:
        """Вспомогательный метод для безопасной фоновой записи"""
        try:
            await self.save_all_supporters_into_db(items)
        except Exception:
            logger.error("Background error saving supporters to db", exc_info=True)
