"""Реестр ачивок (достижений стримеров).

Единственный источник правды о составе ачивок: коды, названия, иконки и
пороги уровней. Условия получения нигде не показываются пользователю —
на профиле видны только уже полученные ачивки (иконка + название).

Механика уровней: пустые ``thresholds`` — одноуровневая ачивка (I);
``thresholds = (5, 10, 25)`` — уровни I/II/III по значению-счётчику
(``level_for`` возвращает 0, пока первый порог не достигнут). Римская
цифра уровня рисуется в углу иконки на профиле.

Механика начисления — два способа (подробнее docs/achievements.md и
``services/achievements.py``):
- **событийные** (``unlock``): пасхалка, звёздочка, трусы — запись в момент
  события;
- **ленивые** (``evaluate``): считаются из БД при просмотре профиля;
  после записи условие больше не перепроверяется (см. правило заморозки).

Добавление новой ачивки: запись в ``ACHIEVEMENTS`` + SVG-иконка
``static/images/achievements/<код>.svg`` + точка начисления (событие или
строка в ``AchievementsService.evaluate_and_get``).
"""

from dataclasses import dataclass

# Поля Links, заполненность которых даёт ачивку «Везде и всюду»
# (memealerts и steam — сервисные ссылки, в «соцсети» не входят).
SOCIAL_LINK_FIELDS: tuple[str, ...] = ("telegram", "youtube", "tiktok", "discord")


@dataclass(frozen=True)
class AchievementDef:
    """Описание ачивки.

    ``code`` — слаг (латиница), совпадает с ``user_achievements.code`` и
    именем файла иконки. ``name`` — название, видимое на профиле только
    после получения. ``icon`` — путь внутри ``static/images/``.
    ``thresholds`` — пороги значений для уровней I/II/...; пусто = один уровень.
    """

    code: str
    name: str
    icon: str
    thresholds: tuple[int, ...] = ()

    @property
    def max_level(self) -> int:
        return len(self.thresholds) or 1


# Событийные (unlock в момент события).
_EVENT_DEFS = (
    AchievementDef("easter_dots", "Ловец точек", "achievements/easter_dots.svg"),
    AchievementDef("star_torn", "Звезда с неба", "achievements/star_torn.svg"),
    AchievementDef("pants_kept", "Вернул своё", "achievements/pants_kept.svg"),
)

# Ленивые: считаются из БД при просмотре профиля (services/achievements.py).
_LAZY_DEFS = (
    AchievementDef("all_rewards", "Полный комплект", "achievements/all_rewards.svg"),
    AchievementDef("memecoins_sold", "Мем-магнат", "achievements/memecoins_sold.svg", (10, 25, 100)),
    AchievementDef("likes_given", "Добрая душа", "achievements/likes_given.svg", (5, 10, 25)),
    AchievementDef("likes_received", "Всеобщая любовь", "achievements/likes_received.svg", (5, 10, 25)),
    AchievementDef("ai_stickers_used", "Стикер-мастер", "achievements/ai_stickers_used.svg", (1, 10, 50)),
    AchievementDef("donated", "Поддержал проект", "achievements/donated.svg"),
    AchievementDef("beta_tester", "Бета-тестер", "achievements/beta_tester.svg"),
    AchievementDef("veteran", "С нами год", "achievements/veteran.svg"),
    AchievementDef("social_links", "Везде и всюду", "achievements/social_links.svg"),
    AchievementDef("telegram_linked", "На связи", "achievements/telegram_linked.svg"),
    AchievementDef("overlay_used", "Декоратор", "achievements/overlay_used.svg"),
)

#: Все ачивки по коду. Порядок — только для чтения человеком; на профиле
#: сортировка по моменту получения (новые слева).
ACHIEVEMENTS: dict[str, AchievementDef] = {d.code: d for d in (*_EVENT_DEFS, *_LAZY_DEFS)}

# Уровневые ачивки: показываем римскую цифру в углу иконки.
LEVELED_ACHIEVEMENT_CODES: frozenset[str] = frozenset(d.code for d in (*_EVENT_DEFS, *_LAZY_DEFS) if d.thresholds)


def level_for(thresholds: tuple[int, ...], value: int) -> int:
    """Уровень ачивки для значения счётчика: 0 — первый порог не достигнут.

    Для одноуровневой ачивки уровень 1 при ``value >= 1``.
    Пороги ожидаются возрастающими.
    """
    if not thresholds:
        return 1 if value >= 1 else 0
    level = 0
    for threshold in thresholds:
        if value >= threshold:
            level += 1
        else:
            break
    return level


def to_roman(n: int) -> str:
    """Римская цифра для уровня (1..39 — больше уровней в реестре нет)."""
    if n <= 0:
        return ""
    numerals = ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
    parts: list[str] = []
    remaining = n
    for value, numeral in numerals:
        while remaining >= value:
            parts.append(numeral)
            remaining -= value
    return "".join(parts)
