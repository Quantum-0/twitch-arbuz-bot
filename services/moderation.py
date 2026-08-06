"""ModerationService — валидация пользовательского ввода на запретки.

Используется в TTSHandler (первый шаг) и при обработке TTS-награды.
В дальнейшем планируется подключить к StickersService (валидация prompt) и к
on_message (автомодерация + автобан повторных нарушителей).

Алгоритм (MVP):
1. Нормализация: lower-case, удалить эмодзи и пунктуацию.
2. De-space: склеить без пробелов (ловит «черепа хохлов» → «черепахохлов»).
3. Транслитерация латиницы → кирилица (ловит «yapi door» → «япидор»).
4. Схлопывание подряд одинаковых символов («доор» → «дор»).
5. Поиск banned-слов как substring в нормализованных вариантах.

TODO (будущее):
- ИИ-модерация через внешний сервис → заполнение ``ModerationResult.confidence``.
- Redis-счётчик нарушений по ``chatter_login`` → автобан при повторе.
- Таблица транслитерации и banned-words вынести в config.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ── Таблица транслитерации латиницы → кириллица ──────────────────────────
# Порядок важен: диграфы/триграфы вперёд, затем одиночные.
_TRANSLIT_RULES: tuple[tuple[str, str], ...] = (
    # Триграфы
    ("sch", "щ"),
    # Диграфы
    ("shch", "щ"),  # на случай «shch» без триграфа
    ("yo", "ё"),
    ("jo", "ё"),
    ("ye", "е"),
    ("yu", "ю"),
    ("ju", "ю"),
    ("ya", "я"),
    ("ja", "я"),
    ("sh", "ш"),
    ("ch", "ч"),
    ("ts", "ц"),
    ("zh", "ж"),
    ("kh", "х"),
    ("th", "т"),
    # Одиночные
    ("a", "а"),
    ("b", "б"),
    ("c", "с"),
    ("d", "д"),
    ("e", "е"),
    ("f", "ф"),
    ("g", "г"),
    ("h", "х"),
    ("i", "и"),
    ("j", "й"),
    ("k", "к"),
    ("l", "л"),
    ("m", "м"),
    ("n", "н"),
    ("o", "о"),
    ("p", "п"),
    ("q", "к"),
    ("r", "р"),
    ("s", "с"),
    ("t", "т"),
    ("u", "у"),
    ("v", "в"),
    ("w", "в"),
    ("x", "кс"),
    ("y", "й"),
    ("z", "з"),
)

# ── Запрещённые слова (русские + английские, Twitch-политика) ─────────────
# Хранятся в нормализованном виде (de-spaced, lower). Расширяемо.
_BANNED_WORDS: tuple[str, ...] = (
    # Русские
    "пидор",
    "пидар",
    "пидорас",
    "пидора",
    "педик",
    "пидорги",
    "негр",
    "нигер",
    "нигга",
    "ниггер",
    "негритос",
    "даун",
    "хохол",
    "хохлов",
    "хохлы",
    "хохляцки",
    # "жид", # Жидкость - забанит(
    # "жыды",
    "чурка",
    "чурки",
    "узкоглаз",
    # Английские (нормализуются через транслит, но проверяем и в англ. варианте)
    "nigger",
    "niger",
    "nigga",
    "faggot",
    "retard",
    "downy",
)


@dataclass(frozen=True)
class ModerationResult:
    """Результат валидации сообщения модерацией.

    ``is_allowed`` — сообщение прошло проверку (можно озвучивать/обрабатывать).
    ``is_banned`` — обнаружена запретка (is_allowed == not is_banned в MVP, но
    поля разделены для будущих категорий: например, «требует ручной проверки»).
    ``found_word`` — найденное запрещённое слово (для варна/лога) или None.
    ``confidence`` — уверенность (1.0 для прямого substring-матча; 0.0 — чисто;
    в будущем будет заполняться ИИ-модерацией).
    """

    is_allowed: bool
    is_banned: bool
    found_word: str | None
    confidence: float


# Эмодзи и пунктуация: удаляем всё, что не буква/цифра/пробел.
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
# Unicode-эмодзи (доп. страховка — \w их не покрывает в некоторых кейсах).
_EMOJI_RE = re.compile("[\U0001f000-\U0001ffff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff]", re.UNICODE)
# Схлопывание подряд одинаковых символов.
_SQUEEZE_RE = re.compile(r"(.)\1+")


class ModerationService:
    """Сервис модерации текста. Singleton в DI-контейнере."""

    def __init__(self) -> None:
        self._banned: tuple[str, ...] = _BANNED_WORDS

    def validate(self, text: str) -> ModerationResult:
        """Проверить текст на запретки. Возвращает frozen ModerationResult."""
        if not text:
            return ModerationResult(is_allowed=True, is_banned=False, found_word=None, confidence=0.0)

        norm = self._normalize(text)
        despaced = norm.replace(" ", "")
        translit = self._translit(despaced)
        # Схлопывание повторов ловит «доор» (из «door») → «дор», «пиидор» → «пидор».
        squeezed = _SQUEEZE_RE.sub(r"\1", translit)

        for variant in (despaced, translit, squeezed):
            for word in self._banned:
                if word in variant:
                    return ModerationResult(
                        is_allowed=False,
                        is_banned=True,
                        found_word=word,
                        confidence=1.0,
                    )
        return ModerationResult(is_allowed=True, is_banned=False, found_word=None, confidence=0.0)

    @staticmethod
    def _normalize(text: str) -> str:
        """lower-case + NFKC-нормализация + удаление эмодзи и пунктуации."""
        s = unicodedata.normalize("NFKC", text).lower()
        s = _EMOJI_RE.sub("", s)
        return _NON_WORD_RE.sub(" ", s)

    @staticmethod
    def _translit(text: str) -> str:
        """Транслитерация латиницы → кириллица по таблице _TRANSLIT_RULES."""
        result = text
        for latin, cyrillic in _TRANSLIT_RULES:
            result = result.replace(latin, cyrillic)
        return result
