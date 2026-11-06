from typing import Annotated, Literal

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Security
from rapidfuzz import fuzz
from sqlalchemy.ext.asyncio import AsyncSession

from container import Container
from database.models import User
from dependencies import get_db
from routers.security_helpers import user_auth
from schemas.api import ChatterSuggestionSchema, UserSearchResponseSchema, UserSearchResultSchema
from twitch.chat.bot import ChatBot
from utils.chat_roles import KNOWN_BOTS
from utils.reference_moderation import REFERENCE_ADMIN_LOGINS, REFERENCE_MODERATOR_LOGINS
from utils.user_search import normalize_query, search_users

router = APIRouter(prefix="/search", tags=["User search"])

# Кого не предлагаем при выдаче бана: стафф, известные боты и сам банящий
BAN_SUGGESTION_EXCLUDES = frozenset(REFERENCE_MODERATOR_LOGINS | REFERENCE_ADMIN_LOGINS | KNOWN_BOTS)

# Порог нечёткого совпадения для чаттеров (имена из чата, без индекса в БД)
_CHATTER_FUZZ_THRESHOLD = 60


def filter_chatters(
    names: list[str],
    query: str,
    exclude_logins: frozenset[str],
    already_suggested_logins: set[str],
    limit: int,
) -> list[ChatterSuggestionSchema]:
    """Отобрать чаттеров канала под запрос: подстрока или нечёткое совпадение.

    Login и display name чаттера совпадают с точностью до регистра, поэтому
    исключения/дедуп матчатся по ``name.lower()``.
    """
    q = normalize_query(query)
    result: list[ChatterSuggestionSchema] = []
    for name in names:
        login = name.lower()
        if login in exclude_logins or login in already_suggested_logins:
            continue
        if q in login or fuzz.ratio(q, login) >= _CHATTER_FUZZ_THRESHOLD:
            result.append(ChatterSuggestionSchema(name=name))
            if len(result) >= limit:
                break
    return result


@router.get("")
@inject
async def search(
    db: Annotated[AsyncSession, Depends(get_db)],
    chat_bot: Annotated[ChatBot, Depends(Provide[Container.chat_bot])],
    user: Annotated[User, Security(user_auth)],
    q: Annotated[str, Query(min_length=2, max_length=64)],
    limit: Annotated[int, Query(ge=1, le=15)] = 10,
    context: Annotated[Literal["generic", "bans", "deposit"], Query()] = "generic",
) -> UserSearchResponseSchema:
    """Нечёткий поиск пользователей по логину.

    context управляет выдачей: `bans` — подсказки для выдачи бана (без стаффа,
    ботов и самого банящего, плюс активные зрители его канала из чат-бота);
    `deposit`/`generic` — только пользователи сайта, без исключений.
    """
    exclude = BAN_SUGGESTION_EXCLUDES | {user.login_name} if context == "bans" else frozenset()
    users = await search_users(db, q, limit=limit, exclude_logins=exclude)
    user_schemas = [UserSearchResultSchema(**item) for item in users]

    chatters: list[ChatterSuggestionSchema] = []
    if context == "bans":
        active = await chat_bot.get_last_active_users(user)
        chatters = filter_chatters(
            [name for name, _ in active],
            q,
            exclude,
            {u["login"] for u in users},
            limit,
        )

    return UserSearchResponseSchema(users=user_schemas, chatters=chatters)
