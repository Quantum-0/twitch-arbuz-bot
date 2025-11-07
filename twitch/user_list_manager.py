import logging
from collections import defaultdict
from time import time

from routers.schemas import ChatMessageWebhookEventSchema


logger = logging.getLogger(__name__)


class UserListManager:
    def __init__(self, max_users_per_channel: int = 300, forget_timeout: float = 3600):
        self._last_messages: dict[str, list[tuple[str, float]]] = defaultdict(
            list
        )  # TODO: replace with Queue or Deque?
        self._last_cleanup: float = time()
        self._forget_timeout: float = forget_timeout
        self._max_users_per_channel: int = max_users_per_channel

    def cleanup(self) -> None:
        q: list[tuple[str, float]]
        dt = time()
        for q in self._last_messages.values():
            while len(q):
                if dt - q[0][1] > self._forget_timeout:
                    del q[0]
                else:
                    break
        self._last_cleanup = dt

    async def handle(self, channel: str, message: ChatMessageWebhookEventSchema):
        user = message.chatter_user_name
        dt = time()
        q = self._last_messages[channel.lower()]

        # Очищаем, если пора
        if dt - self._last_cleanup > 60:
            self.cleanup()

        # Обновляем время и переносим в начало, если нашли
        for i, item in enumerate(q):
            if item[0] == user:
                del q[i]
                q.append((user, dt))
                self._last_messages[channel.lower()] = q
                logger.info(f"Handled msg. Current list: {self._last_messages[channel.lower()]}")
                return

        # Раз не нашли: просто добавляем в начало
        q.append((user, dt))

        # Если вышли за максимальный размер - удаляем первый элемент
        if len(q) > self._max_users_per_channel:
            del q[0]

        self._last_messages[channel.lower()] = q
        logger.info(f"Handled msg. Current list: {self._last_messages[channel.lower()]}")

    def is_user_active(
        self, channel: str, user: str, timeout: float | None = None
    ) -> bool:
        for item in self._last_messages[channel.lower()]:
            if item[0].lower() == user.lower():
                return timeout is None or time() - item[1] < timeout
        return False

    def get_last_active(
        self, channel: str, user: str, timeout: float | None = None
    ) -> float | None:
        for item in self._last_messages[channel.lower()]:
            if item[0].lower() == user.lower():
                if timeout is not None and time() - item[1] > timeout:
                    return None
                return item[1]
        return False

    def get_active_users(
        self, channel: str, timeout: float | None = None
    ) -> list[tuple[str, float]]:
        result = []
        logger.info(f"Generating list of active users with timeout {timeout}")
        logger.info(f"Usrs: {self._last_messages[channel.lower()]}")
        for item in self._last_messages[channel.lower()][::-1]:
            logger.info(f"Check {item}")
            if timeout is not None and time() - item[1] > timeout:
                logger.info("Stop!")
                break
            result.append((item[0], item[1]))
            logger.info("Added")
        return result
