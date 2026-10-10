"""Общий гейт «штатного» доступа (модератор/админ) для страниц и API.

Единая точка проверки вместо дублей списка логинов (раньше список модераторов
был захардкожен вторым экземпляром в ``templates/base.html``). Состав списка
не меняется — источник тот же, что у модерации референсов (о переносе ролей
в БД см. TODO в ``utils/reference_moderation.py``).
"""

from fastapi import HTTPException

from database.models import User
from utils.reference_moderation import is_reference_moderator


def is_staff(user: User | None) -> bool:
    """Модератору/админу видны «Сервер», «Модерация референсов» и бейджи меню."""
    return user is not None and is_reference_moderator(user.login_name)


def ensure_staff(user: User) -> User:
    """Гейт для роутов: 403 для всех, кроме штата."""
    if not is_staff(user):
        raise HTTPException(status_code=403, detail="No access to staff area")
    return user
