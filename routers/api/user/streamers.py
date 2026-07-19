from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from container import Container
from dependencies import get_db
from services.cache import Cache
from twitch.client.twitch import Twitch
from utils.streamers import (
    FILTER_KEYS,
    SortKey,
    SortOrder,
    _parse_tristate,
    get_streamers_list,
    public_streamer_payload,
)

router = APIRouter(prefix="/streamers", tags=["Streamers list"])


VALID_SORTS = ("recommended", "followers", "created", "name")
VALID_ORDERS = ("asc", "desc")


@router.get("")
@inject
async def list_streamers(
    db: Annotated[AsyncSession, Depends(get_db)],
    twitch: Annotated[Twitch, Depends(Provide[Container.twitch])],
    cache: Annotated[Cache, Depends(Provide[Container.cache])],
    sort: Annotated[str, Query()] = "recommended",
    order: Annotated[str, Query()] = "desc",
    f_bot: Annotated[str | None, Query()] = None,
    f_meme: Annotated[str | None, Query()] = None,
    f_ai: Annotated[str | None, Query()] = None,
    f_overlay: Annotated[str | None, Query()] = None,
    f_online: Annotated[str | None, Query()] = None,
    f_pants: Annotated[str | None, Query()] = None,
    f_shoutout: Annotated[str | None, Query()] = None,
):
    """Возвращает список стримеров с фильтрами и сортировкой.

    Query-параметры:
      sort: recommended | followers | created | name
      order: asc | desc (игнорируется при sort=recommended)
      f_bot, f_meme, f_ai, f_overlay, f_online, f_pants, f_shoutout: true | false | null
    """
    sort_key: SortKey = sort if sort in VALID_SORTS else "recommended"  # type: ignore[assignment]
    sort_order: SortOrder = order if order in VALID_ORDERS else "desc"  # type: ignore[assignment]

    raw_filters = {
        "bot": f_bot,
        "meme": f_meme,
        "ai": f_ai,
        "overlay": f_overlay,
        "online": f_online,
        "pants": f_pants,
        "shoutout": f_shoutout,
    }
    filters = {key: _parse_tristate(raw_filters[key]) for key in FILTER_KEYS}

    rows = await get_streamers_list(
        db, twitch, cache, sort=sort_key, order=sort_order, filters=filters
    )
    return [public_streamer_payload(row) for row in rows]
