"""Тесты колоды и тем игры «Найди пару» (оверлей /overlay/pair)."""

from pydantic.color import Color

from database.models import User
from routers.web.overlays import (
    PAIR_GRIDS,
    _arbuz_items,
    _caption_color_for,
    _emoji_items,
    _pair_deck,
    _pair_theme_colors,
)


def _make_user(twitch_id: int, login: str, followers: int = 100) -> User:
    return User(
        twitch_id=str(twitch_id),
        login_name=login,
        profile_image_url=f"https://example.com/{login}.png",
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        followers_count=followers,
    )


async def _seed_streamers(db_session, count: int, followers: int = 100) -> list[str]:
    logins = [f"streamer_{i}" for i in range(count)]
    db_session.add_all([_make_user(1000 + i, login, followers) for i, login in enumerate(logins)])
    await db_session.commit()
    return logins


# ================= колода из стримеров =================


async def test_streamer_deck_size_and_owner(db_session):
    await _seed_streamers(db_session, 12)
    db_session.add(_make_user(42, "channel_owner"))
    await db_session.commit()

    cards = await _pair_deck(db_session, channel_id=42, pairs=10, use_twitch_emoji=False, arbuz=False)

    assert len(cards) == 10
    assert "channel_owner" in {card["id"] for card in cards}
    assert len({card["id"] for card in cards}) == 10, "дубликаты внутри колоды"
    assert all(card["img"].startswith("https://example.com/") for card in cards)


async def test_streamer_deck_skips_small_channels_and_self(db_session):
    # 2 подходящих + 1 с малым чисством фолловеров: набрать 4 пары не выйдет
    await _seed_streamers(db_session, 2)
    db_session.add(_make_user(100, "tiny_channel", followers=10))
    db_session.add(_make_user(42, "channel_owner"))
    await db_session.commit()

    cards = await _pair_deck(db_session, channel_id=42, pairs=4, use_twitch_emoji=False, arbuz=False)

    ids = {card["id"] for card in cards}
    assert "channel_owner" in ids
    assert "tiny_channel" not in ids
    assert len(cards) < 4, "колода не должна раздуваться при нехватке юзеров"


async def test_streamer_deck_exclude_picks_new_streamers(db_session):
    await _seed_streamers(db_session, 12)
    db_session.add(_make_user(42, "channel_owner"))
    await db_session.commit()

    first = await _pair_deck(db_session, channel_id=42, pairs=6, use_twitch_emoji=False, arbuz=False)
    old_faces = [card["id"] for card in first if card["id"] != "channel_owner"]

    second = await _pair_deck(
        db_session, channel_id=42, pairs=6, use_twitch_emoji=False, arbuz=False, exclude=old_faces
    )

    new_ids = {card["id"] for card in second}
    assert "channel_owner" in new_ids
    assert not (set(old_faces) & new_ids), "в новом раунде не должно быть лиц прошлого"


async def test_streamer_deck_exclude_fallback_when_pool_is_small(db_session):
    # пул без исключений не набирается — исключение молча отбрасывается
    await _seed_streamers(db_session, 5)
    db_session.add(_make_user(42, "channel_owner"))
    await db_session.commit()

    cards = await _pair_deck(
        db_session,
        channel_id=42,
        pairs=6,
        use_twitch_emoji=False,
        arbuz=False,
        exclude=["streamer_0", "streamer_1", "streamer_2", "streamer_3", "streamer_4"],
    )

    assert len(cards) == 6


# ================= стикеры и эмодзи =================


def test_arbuz_items_read_directory_dynamically():
    items = _arbuz_items()
    # в репозитории лежит минимум 10 стикеров + LICENSE.md (не картинка)
    assert len(items) >= 10
    names = [item_id.removeprefix("arbuz:") for item_id, _caption, _img in items]

    assert "LICENSE.md" not in names
    captions = {item_id.removeprefix("arbuz:"): caption for item_id, caption, _img in items}
    assert captions["1.webp"] == "Вкусьни"
    assert captions["8.png"] == "Кексик"
    # натуральная сортировка: 2 идёт раньше 10
    assert names.index("2.webp") < names.index("10.webp")
    assert all(img.startswith("/static/images/stickers/") for _id, _caption, img in items)


def test_emoji_items_extended_enough_for_biggest_grid():
    # 5x5 = 12 пар — список эмодзи должен покрывать её целиком
    items = _emoji_items()
    assert len(items) >= 13
    assert len({item_id for item_id, _caption, _img in items}) == len(items), "эмоуты должны быть уникальны"


async def test_pair_deck_emoji_mode_respects_pairs():
    cards = await _pair_deck(None, channel_id=1, pairs=8, use_twitch_emoji=True, arbuz=False)
    assert len(cards) == 8
    assert all(card["img"].startswith("https://static-cdn.jtvnw.net/") for card in cards)


async def test_pair_deck_arbuz_mode_caps_at_available():
    cards = await _pair_deck(None, channel_id=1, pairs=12, use_twitch_emoji=False, arbuz=True)
    assert len(cards) == min(12, len(_arbuz_items()))


# ================= темы =================


def test_pair_grids_cover_all_sizes():
    assert set(PAIR_GRIDS) == {"3x4", "4x3", "5x4", "4x5", "5x5", "4x4", "3x3", "2x5", "5x2"}
    assert PAIR_GRIDS["3x4"] == (3, 4)
    assert PAIR_GRIDS["5x2"] == (5, 2)


def test_preset_theme_returns_full_color_set():
    colors = _pair_theme_colors("dark", None, None, None, None)
    assert set(colors) == {"front1", "front2", "back1", "back2", "caption", "glow", "shadow"}


def test_custom_theme_overrides_colors_and_derives_caption():
    colors = _pair_theme_colors(
        "custom",
        Color("#123456"),
        Color("#654321"),
        Color("#101010"),
        Color("#202020"),
    )
    assert colors["front1"] == "#123456"
    assert colors["back2"] == "#202020"
    assert colors["glow"] == "#123456", "свечение — цветом рубашки"
    assert colors["caption"] == "#f5f5f7", "на тёмном фоне ник светлый"


def test_custom_theme_without_colors_falls_back_to_sunset():
    colors = _pair_theme_colors("custom", None, None, None, None)
    assert colors["front1"] == "#ff8a00"
    assert colors["back2"] == "#00f2fe"


def test_caption_color_by_luminance():
    assert _caption_color_for("#ffffff") == "#2b2b33"
    assert _caption_color_for("#000000") == "#f5f5f7"
