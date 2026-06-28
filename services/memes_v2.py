import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Generic, TypeVar

import httpx
import sqlalchemy as sa
from memealerts.types.exceptions import MATokenExpiredError
from memealerts.types.user_id import UserID
from opentelemetry import trace
from pydantic import BaseModel, PrivateAttr, ValidationError, computed_field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from config import memealerts_scope, settings
from database.models import MemealertsSettings, User
from exceptions import MARefreshTokenError, MAInvalidTokenError, MAUnavailableError, MAValidationRespError
from schemas.api import BoolResponseSchema
from schemas.memealerts import MAUserInfo

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

    async def give_bonus(self, ma_token: Tokens[str], user_id: UserID, value: int) -> bool:
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
            if response.status_code != 201:
                logger.error(f"Access error from MA. Resp: {response.json()}")
                raise MAInvalidTokenError
            data = response.json()
            try:
                return BoolResponseSchema(**data).result
            except ValidationError as exc:
                raise MAValidationRespError from exc

    async def resolve_user_input_to_user_id(self, ma_token, user_input: str):
        # TODO: convert user input to user_id
        pass
