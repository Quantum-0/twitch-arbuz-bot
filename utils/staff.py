"""Общий гейт «штатного» доступа (модератор/админ).

Единая точка проверки вместо дублей списка логинов (раньше список модераторов
был захардкожен вторым экземпляром в ``templates/base.html``). Состав списка
не меняется — источник тот же, что у модерации референсов (о переносе ролей
в БД см. TODO в ``utils/reference_moderation.py``).

Используется: Jinja-global в шаблонах (``utils/template_globals.py``) и
Security-зависимость ``routers.security_helpers.staff_auth``.
"""

from database.models import User
from utils.reference_moderation import is_reference_moderator


def is_staff(user: User | None) -> bool:
    """Модератору/админу видны «Сервер», «Модерация референсов» и бейджи меню."""
    return user is not None and is_reference_moderator(user.login_name)
