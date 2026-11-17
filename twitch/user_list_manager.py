import logging
from collections import defaultdict, deque
from time import time

from opentelemetry import trace

from schemas.twitch import ChatMessageWebhookEventSchema

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class UserListManager:
    def __init__(self, max_users_per_channel: int = 300, forget_timeout: float = 3600):
        # (display_name, twitch_id, last_active_ts) — id храним рядом с именем:
        # ключи стейта по имени коллизятся (одинаковые display name, смена ника),
        # см. TARGET_COOLDOWN в PantsCommand.
        self._last_messages: dict[str, deque[tuple[str, int, float]]] = defaultdict(deque)
        self._last_cleanup: float = time()
        self._forget_timeout: float = forget_timeout
        self._max_users_per_channel: int = max_users_per_channel

    def cleanup(self) -> None:
        q: deque[tuple[str, int, float]]
        dt = time()
        for q in self._last_messages.values():
            while q:
                if dt - q[0][2] > self._forget_timeout:
                    q.popleft()
                else:
                    break
        self._last_cleanup = dt

    @tracer.start_as_current_span("ChatBot: Handle User List")
    async def handle(self, channel: str, message: ChatMessageWebhookEventSchema):
        user = message.chatter_user_name
        user_id = message.chatter_user_id
        dt = time()
        q = self._last_messages[channel.lower()]

        # Очищаем, если пора
        if dt - self._last_cleanup > 60:
            self.cleanup()

        # Обновляем время и переносим в начало, если нашли
        for i, item in enumerate(q):
            if item[0] == user:
                del q[i]
                q.append((user, user_id, dt))
                self._last_messages[channel.lower()] = q
                return

        # Раз не нашли: просто добавляем в начало
        q.append((user, user_id, dt))

        # Если вышли за максимальный размер - удаляем первый элемент
        if len(q) > self._max_users_per_channel:
            q.popleft()

        self._last_messages[channel.lower()] = q

    def is_user_active(self, channel: str, user: str, timeout: float | None = None) -> bool:
        for item in self._last_messages[channel.lower()]:
            if item[0].lower() == user.lower():
                return timeout is None or time() - item[2] < timeout
        return False

    def get_last_active(self, channel: str, user: str, timeout: float | None = None) -> float | None:
        for item in self._last_messages[channel.lower()]:
            if item[0].lower() == user.lower():
                if timeout is not None and time() - item[2] > timeout:
                    return None
                return item[2]
        return False

    def get_active_entries(self, channel: str, timeout: float | None = None) -> list[tuple[str, int, float]]:
        """Активные чаттеры канала: (display_name, twitch_id, last_active_ts), свежие первыми."""
        result: list[tuple[str, int, float]] = []
        for item in reversed(self._last_messages[channel.lower()]):
            if timeout is not None and time() - item[2] > timeout:
                break
            result.append(item)
        return result

    def get_active_users(self, channel: str, timeout: float | None = None) -> list[tuple[str, float]]:
        return [(name, ts) for name, _, ts in self.get_active_entries(channel, timeout)]
