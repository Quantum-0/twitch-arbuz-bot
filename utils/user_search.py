"""Нечёткий поиск пользователей по логину: pg_trgm (SQL) + rapidfuzz (ранжирование).

Двухэтапная схема:
  1. SQL-пул кандидатов через GIN-индекс pg_trgm (оператор `%` + ILIKE по подстроке);
  2. переранжирование пула rapidfuzz (`fuzz.ratio` + бонус за префикс) — точнее
     триграммной similarity на коротких запросах и опечатках вида «kwantum075».
"""

from collections.abc import Collection

import sqlalchemy as sa
from rapidfuzz import fuzz
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User

# Запросы короче двух символов не ищем — совпадения слишком шумные
MIN_QUERY_LENGTH = 2
# Сколько кандидатов достаём из SQL до переранжирования rapidfuzz. Пул порядка
# по trgm-similarity может выкинуть логин с низкой триграммной похожестью, но
# высоким fuzz.ratio — берём с запасом (GIN-индекс тянет и больше).
_CANDIDATES_POOL = 200


def normalize_query(query: str) -> str:
    return query.strip().lstrip("@").lower()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _score(query: str, login: str) -> float:
    score = float(fuzz.ratio(query, login))
    if login.startswith(query):
        score += 10
    return score


async def search_users(
    db: AsyncSession,
    query: str,
    limit: int = 10,
    exclude_logins: Collection[str] = frozenset(),
) -> list[dict[str, str]]:
    """Нечёткий поиск пользователей по логину.

    Возвращает список словарей `{login, twitch_id, avatar_url}`, отсортированный
    по убыванию похожести. `exclude_logins` — логины в нижнем регистре.
    """
    q = normalize_query(query)
    if len(q) < MIN_QUERY_LENGTH or limit <= 0:
        return []

    stmt = (
        sa.select(User.login_name, User.twitch_id, User.profile_image_url)
        .where(
            sa.or_(
                # pg_trgm similarity (порог по умолчанию 0.3)
                User.login_name.op("%")(q),
                User.login_name.ilike(f"%{_escape_like(q)}%", escape="\\"),
            )
        )
        .order_by(sa.func.similarity(User.login_name, q).desc())
        .limit(_CANDIDATES_POOL)
    )
    if exclude_logins:
        stmt = stmt.where(User.login_name.notin_(exclude_logins))

    rows = (await db.execute(stmt)).all()
    ranked = sorted(rows, key=lambda row: (-_score(q, row.login_name), len(row.login_name), row.login_name))
    return [
        {"login": row.login_name, "twitch_id": row.twitch_id, "avatar_url": row.profile_image_url}
        for row in ranked[:limit]
    ]
