import math
import random
from datetime import datetime

DAY = 24 * 60 * 60


def _memealerts_reward_score(usr: dict) -> float:
    """Бонус за активность награды мемкоинов (агрегаты memealerts_deposits).

    Только успешные пополнения. Накопительные метрики — за скользящие 90 дней,
    недельные — за 7 дней. Пороги согласованы с владельцем:
    - 3–20 пополнений — награда есть и использовалась (+0.1), 21+ — активно (+0.2);
    - 3–9 уникальных зрителей (+0.1), 10+ (+0.2) — граница 10 в пользу верхнего тира;
    - за неделю 1–99 мемкоинов — хороший спрос при не слишком низкой цене (+0.2),
      100+ — награда слишком дешёвая (+0.1);
    - ≥3 уникальных зрителей за неделю — живой текущий спрос (+0.1);
    - анти-халява: ≥10 пополнений и один зритель сделал больше половины (−0.1).
    Поля читаются через .get: словари без deposit-агрегатов (старые тесты) не падают.
    """
    deposits = usr.get("deposits_count", 0) or 0
    users = usr.get("deposits_users", 0) or 0
    top1 = usr.get("deposits_top1_count", 0) or 0
    weekly_coins = usr.get("deposits_weekly_coins", 0) or 0
    weekly_users = usr.get("deposits_weekly_users", 0) or 0

    score = 0.0
    if 3 <= deposits <= 20:
        score += 0.1
    elif deposits >= 21:
        score += 0.2

    if 3 <= users < 10:
        score += 0.1
    elif users >= 10:
        score += 0.2

    if 0 < weekly_coins < 100:
        score += 0.2
    elif weekly_coins >= 100:
        score += 0.1

    if weekly_users >= 3:
        score += 0.1

    if deposits >= 10 and top1 > deposits / 2:
        score -= 0.1
    return score


def compute_streamer_score(usr: dict) -> float:
    now = datetime.now()
    return (
        (10 * bool(usr["is_live"]))
        + (4 * bool((now - usr["created_at"]).total_seconds() < DAY))  # Зареган меньше суток назад
        + (1.75 * bool((now - usr["created_at"]).total_seconds() < 7 * DAY))  # Зареган меньше недели назад
        + (
            1.75 * bool((now - usr["interacted_at"]).total_seconds() < 30 * DAY)
        )  # Заходил в панель управления ботом за последний месяц
        + (
            2 * bool((now - usr["interacted_at"]).total_seconds() < DAY)
        )  # Заходил в панель управления ботом за последние сутки
        + (0.6 * math.log10((usr.get("followers", 0) or 0) + 1))
        + (2 * (usr["username"] == "quantum075" or usr["donated"] > 0))
        + (1 * usr["is_beta_tester"])
        + (2 * usr["memealerts_enabled"])
        + (3 * usr["chat_bot_enabled"])
        + (4 * usr["ai_stickers_enabled"])
        + min(2, usr.get("likes_count", 0) * 0.05)
        # +0.05 за каждую полученную ачивку (естественный потолок — размер
        # реестра utils/achievements.py, сверху не ограничиваем).
        + (0.05 * usr.get("achievements_count", 0))
        + _memealerts_reward_score(usr)
        + (5 * random.random())
    )
