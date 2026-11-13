"""Каталог мок-контекстов страниц — одна точка правды для фронтенда без бэка.

Используется:
  * тестами рендера шаблонов (tests/front/test_templates.py);
  * браузерными тестами (мини-сервер из server.py отдаёт страницы каталога);
  * дев-сервером без бэка (python -m tests.front.dev_server).

Здесь живёт изолированное Jinja2-окружение (те же глобалы, что у приложения:
static_url/site_url/is_staff) со StrictUndefined — рендер падает при обращении
к несуществующей переменной шаблона, а не молча выводит пустоту.
"""

from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from fastapi.templating import Jinja2Templates
from jinja2 import StrictUndefined

from routers.web.pages import TTS_ROLE_ICONS, TTS_ROLE_LABELS, TTS_TRIGGER_KEYS
from utils.template_globals import register_template_globals

# 1x1 прозрачный PNG — безопасная подстановка для аватарок/картинок.
AVATAR_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAf"
    "FcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

TEMPLATES_ENV = Jinja2Templates(directory="templates")
register_template_globals(TEMPLATES_ENV)
TEMPLATES_ENV.env.undefined = StrictUndefined


class MockRequest:
    """Минимальная замена starlette.Request для рендера шаблонов."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.url = base_url
        self.query_params: dict[str, str] = {}
        self.session: dict[str, Any] = {}


# ── Фабрики мок-объектов ─────────────────────────────────────────────────────

# Командные тумблеры чат-бота (templates/panel/chatbot.html).
_CHATBOT_TOGGLE_SETTINGS = [
    "enable_bite",
    "enable_lick",
    "enable_boop",
    "enable_pat",
    "enable_scratch",
    "enable_hug",
    "enable_banana",
    "enable_lurk",
    "enable_horny_good",
    "enable_tail",
    "enable_dice",
    "enable_bonk",
    "enable_pants",
    "enable_whoami",
    "enable_treat",
    "enable_feed",
    "enable_pasta",
    "enable_tg_link",
    "enable_ds_link",
    "enable_tiktok_link",
    "enable_youtube_link",
    "enable_memealerts_link",
    "enable_links_command",
    "enable_steam_link",
]


def make_settings(**overrides: Any) -> SimpleNamespace:
    settings: dict[str, Any] = {
        # чат-бот
        "enable_chat_bot": True,
        "allow_shared_chat": False,
        "chatbot_default_target_behaviour": "tip",
        "enable_shoutout_on_raid": False,
        # ИИ-стикеры (templates/ai_stickers.html)
        "ai_sticker_model": "mini",
        "ai_reference_usage_policy": "allow",
        "ai_stickers_show_in_profile": True,
        "ai_require_own_character": False,
        "ai_reference_show_in_profile": True,
        "ai_reference_allow_on_other_channels": True,
    }
    settings.update(dict.fromkeys(_CHATBOT_TOGGLE_SETTINGS, True))
    settings.update(overrides)
    return SimpleNamespace(**settings)


def make_user(**overrides: Any) -> SimpleNamespace:
    """Мок User с атрибутным покрытием base.html + страниц каталога."""
    user = SimpleNamespace(
        id=1,
        twitch_id=42,
        login_name="mock_user",
        profile_image_url=AVATAR_PNG,
        in_beta_test=False,
        balance=100,
        followers_count=10,
        boosty_dismissed_at=None,
        boosty_offer_eligible=False,
        followed_to_admin=False,
        settings=make_settings(),
        telegram=SimpleNamespace(telegram_user_id=None, telegram_username=None),
        links=SimpleNamespace(steam_id=None),
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


def make_tts_settings(**overrides: Any) -> SimpleNamespace:
    tts = SimpleNamespace(
        enabled=True,
        read_username=True,
        model="neco-arc",
        max_length=300,
        cooldown_per_user=60,
        cooldown_per_channel=10,
        permissions=SimpleNamespace(roles={}),
        external_key="mock-key",
    )
    for key, value in overrides.items():
        setattr(tts, key, value)
    return tts


# ── Контексты страниц ────────────────────────────────────────────────────────


def _main(base_url: str) -> dict[str, Any]:
    # user=None явно: StrictUndefined строг даже к {% if user %} (в проде туда
    # просто не передают user, дефолтный Undefined молча даёт falsy).
    return {"request": MockRequest(base_url), "users_count": 1234, "user": None}


def _panel(base_url: str) -> dict[str, Any]:
    user = make_user(
        telegram=SimpleNamespace(telegram_user_id=99, telegram_username="mock_tg"),
        links=SimpleNamespace(steam_id="7656119"),
    )
    return {
        "request": MockRequest(base_url),
        "user": user,
        "settings": user.settings,
        "memealerts": {
            "enabled": True,
            "enabled_v2": True,
            "coins_for_reward": 10,
            "currency": ["мемкоина", "мемкоин", "мемкоинов", "мемкоины"],
        },
        "slovotron_secret": "mock-secret",
        "tg_clips_beta": True,
        "telegram_login_enabled": True,
        # Бейджи главной /panel (panel.html).
        "hub": {"chatbot": True, "memealerts": True, "telegram": True},
    }


def _tts(base_url: str) -> dict[str, Any]:
    return {
        "request": MockRequest(base_url),
        "user": make_user(),
        "tts": make_tts_settings(),
        "tts_roles": dict(TTS_ROLE_LABELS),
        "tts_role_icons": dict(TTS_ROLE_ICONS),
        "trigger_keys": list(TTS_TRIGGER_KEYS),
    }


def _ai_stickers(base_url: str) -> dict[str, Any]:
    return {
        "request": MockRequest(base_url),
        "user": make_user(),
        "settings": make_settings(),
        "reference": SimpleNamespace(
            description="маленькая зелёная арбузная лисичка", approved=True, file_id="ref.png"
        ),
        "reference_upload_allowed": True,
        "ai_stickers": [],
    }


def _user_only(base_url: str) -> dict[str, Any]:
    return {"request": MockRequest(base_url), "user": make_user()}


def _ui_demo(base_url: str) -> dict[str, Any]:
    return {"request": MockRequest(base_url), "user": None}


def _streamers(base_url: str) -> dict[str, Any]:
    return {"request": MockRequest(base_url), "user": make_user(), "streamers": []}


def _faq(base_url: str) -> dict[str, Any]:
    return {
        "request": MockRequest(base_url),
        "user": None,
        "faq_items": [
            {"question": "Кто такой бот?", "answer": "Бот для чата Twitch."},
            {"question": "Сколько стоит?", "answer": "Бесплатно."},
        ],
    }


def _roadmap(base_url: str) -> dict[str, Any]:
    return {
        "request": MockRequest(base_url),
        "user": make_user(),
        "roadmap": [
            {"date": "Ноябрь 2026", "text": "Рефакторинг панели на веб-компоненты"},
            {"date": "Декабрь 2026", "text": "Что-нибудь ещё"},
        ],
        "todos": ["TODO из мока"],
    }


def _cmdlist(base_url: str) -> dict[str, Any]:
    return {
        "request": MockRequest(base_url),
        "user": make_user(),
        "streamer_name": "mock_user",
        "streamer_pic": AVATAR_PNG,
        "commands": [
            ("!кусь", "кусить, кус", "Укусить зрителя"),
            ("!обнять", "hug", "Обнять зрителя"),
        ],
    }


@dataclass(frozen=True)
class PageSpec:
    url_path: str
    template: str
    context: Callable[[str], dict[str, Any]]


PAGES: tuple[PageSpec, ...] = (
    PageSpec("/", "main.html", _main),
    PageSpec("/panel", "panel.html", _panel),
    # Разделы панели: общий мок-контекст _panel (StrictUndefined требует
    # все переменные каркаса panel/base_section.html).
    PageSpec("/panel/chatbot", "panel/chatbot.html", _panel),
    PageSpec("/panel/memealerts", "panel/memealerts.html", _panel),
    PageSpec("/panel/telegram", "panel/telegram.html", _panel),
    PageSpec("/panel/overlays", "panel/overlays.html", _panel),
    PageSpec("/panel/other", "panel/other.html", _panel),
    PageSpec("/panel/tts", "tts.html", _tts),
    PageSpec("/panel/ai-stickers", "ai_stickers.html", _ai_stickers),
    PageSpec("/ui-demo", "ui-demo.html", _ui_demo),
    PageSpec("/streamers", "streamers.html", _streamers),
    PageSpec("/stats", "stats.html", _user_only),
    PageSpec("/balance-history", "balance_history.html", _user_only),
    PageSpec("/bans", "bans.html", _user_only),
    PageSpec("/about", "about.html", _user_only),
    PageSpec("/faq", "faq.html", _faq),
    PageSpec("/kinda_roadmap", "roadmap.html", _roadmap),
    PageSpec("/cmdlist", "streamer-commands.html", _cmdlist),
)

PAGES_BY_PATH: dict[str, PageSpec] = {spec.url_path: spec for spec in PAGES}

# Смоук-маркеры: что обязано быть в отрендеренном HTML страницы.
PAGE_MARKERS: dict[str, tuple[str, ...]] = {
    "/panel": (
        "panel-hub",
        "/panel/chatbot",
        "/panel/memealerts",
        "/panel/telegram",
        "/panel/ai-stickers",
        "/panel/tts",
        "/panel/overlays",
        "/panel/other",
        "resizer.quantum0.ru",
        "cut.quantum0.ru",
    ),
    "/panel/chatbot": ("q0ui-switch", "command-category", "toggle-all-btn", "command-help", "cmd-category-links-grid"),
    "/panel/memealerts": ("q0ui-status-card", "coin-count", "memealerts-reward-btn", "reward-cost", "Название валюты"),
    "/panel/telegram": ("telegram-panel", "tg-link-btn", "tg-stream-notif-toggle"),
    "/panel/overlays": ("overlay-card", "q0ui-slider", "overlay-link", "panel/tts"),
    "/panel/other": ("enable_shoutout_on_raid", "reset-overlay-secret-btn", "steam-unlink-btn"),
    "/panel/tts": ("toggle-switch", "tts.js"),
    "/panel/ai-stickers": ("balance-card", "ai-stickers.js"),
    "/ui-demo": ("q0ui-switch", "q0ui-slider", "ui/index.js"),
    "/streamers": ("streamerCardTemplate",),
    "/stats": ("chart", "stats.js"),
    "/balance-history": ("balance-current-banner", "balance_history.js"),
    "/bans": ("user-suggest.js",),
    "/faq": ("Кто такой бот",),
    "/kinda_roadmap": ("Рефакторинг панели",),
    "/cmdlist": ("!кусь",),
}


def render_page(url_path: str, base_url: str) -> str:
    """Рендер страницы каталога. Поднимает исключения Jinja как есть."""
    spec = PAGES_BY_PATH[url_path]
    context = spec.context(base_url)
    return TEMPLATES_ENV.get_template(spec.template).render(**context)
