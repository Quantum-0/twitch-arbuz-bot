"""Хелперы для TTS-настроек: lazy-доступ к TTSSettings с дефолтами и lazy-создание строки."""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DEFAULT_TTS_PERMISSIONS, TTSSettings, User

# Дефолтные значения скалярных полей (когда строки TTSSettings ещё нет).
DEFAULT_TTS = {
    "enabled": False,
    "read_username": False,
    "cooldown_per_user": 0,
    "cooldown_per_channel": 0,
    "max_length": 500,
    "model": None,
    "external_key": None,
}

# Удаление unicode-эмодзи (Twitch-эмодзи уже отфильтрованы по fragment.type).
_EMOJI_RE = re.compile("[\U0001f000-\U0001ffff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff]", re.UNICODE)
# Схлопывание пробелов.
_WS_RE = re.compile(r"\s+")


def clean_tts_text(text: str) -> str:
    """Очистить текст перед озвучкой: удалить эмодзи, схлопнуть пробелы, trim."""
    s = _EMOJI_RE.sub("", text)
    return _WS_RE.sub(" ", s).strip()


def truncate_tts(text: str, max_length: int) -> str:
    """Обрезать текст до max_length на ближайшем пробеле.
    Если обрезка произошла — дописать « и ещё много много букв»."""
    if len(text) <= max_length:
        return text
    suffix = " и ещё много много букв"
    # Оставляем запас под suffix.
    limit = max_length - len(suffix)
    if limit <= 0:
        return text[:max_length]
    cut = text[:limit]
    # Откатываемся до последнего пробела, чтобы не разрезать слово.
    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return cut + suffix


def get_tts_settings(user: User) -> TTSSettings:
    """Вернуть TTSSettings пользователя. Если строки ещё нет — вернуть
    transient-объект, заполненный дефолтами (без записи в БД)."""
    if user.tts is not None:
        return user.tts
    return TTSSettings(
        user_id=user.id,  # type: ignore[arg-type]
        enabled=DEFAULT_TTS["enabled"],
        tts_reward_id=None,
        read_username=DEFAULT_TTS["read_username"],
        permissions=DEFAULT_TTS_PERMISSIONS,
        cooldown_per_user=DEFAULT_TTS["cooldown_per_user"],
        cooldown_per_channel=DEFAULT_TTS["cooldown_per_channel"],
        max_length=DEFAULT_TTS["max_length"],
        model=DEFAULT_TTS["model"],
        external_key=DEFAULT_TTS["external_key"],
    )


async def ensure_tts_settings(db: AsyncSession, user: User) -> TTSSettings:
    """Гарантировать наличие строки TTSSettings в БД. Если её нет — создать и
    зафиксировать. Возвращает persisted-объект (привязанный к user.tts)."""
    if user.tts is not None:
        return user.tts
    obj = TTSSettings(
        user_id=user.id,
        enabled=DEFAULT_TTS["enabled"],
        tts_reward_id=None,
        read_username=DEFAULT_TTS["read_username"],
        permissions=DEFAULT_TTS_PERMISSIONS,
        cooldown_per_user=DEFAULT_TTS["cooldown_per_user"],
        cooldown_per_channel=DEFAULT_TTS["cooldown_per_channel"],
        max_length=DEFAULT_TTS["max_length"],
        model=DEFAULT_TTS["model"],
        external_key=DEFAULT_TTS["external_key"],
    )
    db.add(obj)
    user.tts = obj
    await db.flush()
    return obj
