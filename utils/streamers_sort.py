import math
import random
from datetime import datetime

DAY = 24 * 60 * 60


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
        + (5 * random.random())
    )
