import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from redis.asyncio import Redis

from twitch.state_manager import (
    COMMAND_TYPE,
    COMMON_CHANNEL,
    COMMON_COMMAND,
    COMMON_USER,
    PARAM_TYPE,
    USER_TYPE,
    VALUE_TYPE,
    SMParam,
    StateManager,
)

logger = logging.getLogger(__name__)


class RedisStateManager(StateManager):
    def __init__(self, redis: Redis, default_ttl: int = 4 * 60 * 60):
        self._r = redis
        self._default_ttl = default_ttl

    async def get_state(
        self,
        *,
        channel: str = COMMON_CHANNEL,
        user: int = COMMON_USER,
        command: str = COMMON_COMMAND,
        param: PARAM_TYPE = PARAM_TYPE.DEFAULT,
    ) -> VALUE_TYPE:
        result = await self.get_from_redis(channel=channel, user=user, command=command, param=param)
        if len(result.keys()) == 0:
            return None
        if len(result.keys()) > 1:
            raise KeyError
        return list(result.values())[0]

    async def get_from_redis(
        self,
        *,
        channel: str | None = None,
        user: int | None = None,
        command: str | None = None,
        param: PARAM_TYPE | None = None,
    ) -> dict[str, VALUE_TYPE]:
        try:
            if channel and command and user:
                exact_key = f"sm:{channel}:{command}:{user}:{param}"
                value = await self._r.get(exact_key)
                return {self._ensure_str(exact_key): await self._decode_value(value)} if value else {}

            sets_to_intersect = []
            if channel:
                sets_to_intersect.append(f"idx:channel:{channel}")
            if command:
                sets_to_intersect.append(f"idx:command:{command}")
            if user:
                sets_to_intersect.append(f"idx:user:{user}")
            if param:
                sets_to_intersect.append(f"idx:param:{param}")

            if not sets_to_intersect:
                target_keys = [key async for key in self._r.scan_iter(match="sm:*:*:*:*")]
            else:
                target_keys = await self._r.sinter(sets_to_intersect)

            if not target_keys:
                return {}

            values = await self._r.mget(target_keys)
            return {
                self._ensure_str(key): await self._decode_value(val)
                for key, val in zip(target_keys, values, strict=True)
                if val is not None
            }
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.error(f"Redis недоступен! Ошибка: {e}")
            return {}

    @staticmethod
    def _ensure_str(value: str | bytes) -> str:
        if isinstance(value, bytes):
            str_value = value.decode("utf-8")
        elif isinstance(value, str):
            str_value = value
        else:
            raise TypeError("Invalid type")
        return str_value

    @staticmethod
    async def _encode_value(value: VALUE_TYPE) -> str | None:
        if isinstance(value, float):
            return f"f{value}"
        if isinstance(value, int):
            return f"i{value}"
        if isinstance(value, str):
            return f"s{value}"
        if value is None:
            return ""
        raise TypeError("Invalid type")

    @classmethod
    async def _decode_value(cls, value: str | bytes) -> VALUE_TYPE:
        str_value = cls._ensure_str(value)
        if str_value == "":
            return None
        if str_value[0] == "s":
            return str_value[1:]
        if str_value[0] == "f":
            return float(str_value[1:])
        if str_value[0] == "i":
            return int(str_value[1:])
        raise TypeError

    async def set_state(
        self,
        value: VALUE_TYPE,
        *,
        channel: str = COMMON_CHANNEL,
        user: int = COMMON_USER,
        command: str = COMMON_COMMAND,
        param: PARAM_TYPE = PARAM_TYPE.DEFAULT,
    ) -> None:
        main_key = f"sm:{channel}:{command}:{user}:{param}"
        redis_value: str | None = await self._encode_value(value)
        try:
            if redis_value is None:
                await self.del_state(channel=channel, user=user, command=command, param=param)
            else:
                await self._r.set(main_key, redis_value, ex=self._default_ttl)
                await self._r.sadd(f"idx:channel:{channel}", main_key)
                await self._r.sadd(f"idx:command:{command}", main_key)
                await self._r.sadd(f"idx:user:{user}", main_key)
                await self._r.sadd(f"idx:param:{param}", main_key)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.error(f"Redis недоступен! Ошибка: {e}")
            return

    async def del_state(
        self,
        *,
        channel: str = COMMON_CHANNEL,
        user: int = COMMON_USER,
        command: str = COMMON_COMMAND,
        param: PARAM_TYPE = PARAM_TYPE.DEFAULT,
    ) -> int:
        try:
            if channel and command and user:
                exact_key = f"sm:{channel}:{command}:{user}:{param}"
                return await self._r.delete(exact_key)

            sets_to_intersect = []
            if channel:
                sets_to_intersect.append(f"idx:channel:{channel}")
            if command:
                sets_to_intersect.append(f"idx:command:{command}")
            if user:
                sets_to_intersect.append(f"idx:user:{user}")
            if param:
                sets_to_intersect.append(f"idx:param:{param}")

            if not sets_to_intersect:
                target_keys = [key async for key in self._r.scan_iter(match="sm:*:*:*:*")]
            else:
                target_keys = await self._r.sinter(sets_to_intersect)

            if not target_keys:
                return 0

            return await self._r.delete(*target_keys)

        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.error(f"Redis недоступен! Ошибка: {e}")
            return 0

    async def get_all_from_channel(
        self, *, channel: str = COMMON_CHANNEL
    ) -> AsyncIterator[tuple[USER_TYPE, COMMAND_TYPE, PARAM_TYPE, VALUE_TYPE]]:
        data = await self.get_from_redis(channel=channel)
        for redis_key, value in data.items():
            if not redis_key.startswith("sm:"):
                continue
            parts = redis_key.split(":")
            if len(parts) != 5:
                continue
            user: int
            sm, channel, command, user, param = parts
            if sm != "sm":
                continue
            if user.isdigit():  # type: ignore
                user = int(user)
            yield user, command, SMParam(param), value

    async def cleanup(self):
        pass

    @asynccontextmanager
    async def lifespan(self):
        listener_task = asyncio.create_task(self.redis_event_listener(self._r))
        logger.info("Успешно: Подключение к Redis установлено, фоновый очиститель запущен.")
        yield
        logger.info("Остановка приложения: завершаем фоновые задачи...")
        listener_task.cancel()  # Сигнализируем воркеру завершить работу
        try:
            await listener_task  # Ожидаем завершения таски
        except asyncio.CancelledError:
            pass

        await self._r.aclose()  # Закрываем пул соединений с Redis
        logger.info("Успешно: Соединения с Redis закрыты.")


    # --- ФОНОВЫЙ ВОРКЕР ДЛЯ СЛУШАНИЯ СОБЫТИЙ ОЧИСТКИ ---
    @staticmethod
    async def redis_event_listener(client: aioredis.Redis):
        pubsub = None
        while True:  # Бесконечный цикл на случай падения Redis
            try:
                pubsub = client.pubsub()
                await pubsub.subscribe("__keyevent@0__:expired", "__keyevent@0__:del")

                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue

                    expired_key = message["data"]
                    if expired_key.startswith("sm:"):
                        parts = expired_key.split(":")
                        if len(parts) == 5:
                            sm, channel, command, user, param = parts
                            if sm != "sm":
                                continue

                            async with client.pipeline(transaction=True) as pipe:
                                logger.debug("REM %s", expired_key)
                                pipe.srem(f"idx:channel:{channel}", expired_key)  # noqa
                                pipe.srem(f"idx:command:{command}", expired_key)  # noqa
                                pipe.srem(f"idx:user:{user}", expired_key)  # noqa
                                pipe.srem(f"idx:param:{param}", expired_key)  # noqa
                                await pipe.execute()
            except (aioredis.ConnectionError, aioredis.TimeoutError):
                logger.warning("Связь с Redis потеряна. Ожидание 5 секунд для переподключения...")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                if pubsub:
                    await pubsub.unsubscribe("__keyevent@0__:expired", "__keyevent@0__:del")
                break


def init_redis(redis_url: str) -> Redis:
    return aioredis.from_url(redis_url, decode_responses=True)
#
# # --- МЕНЕДЖЕР ЖИЗНЕННОГО ЦИКЛА (LIFESPAN) ---
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     global redis_client
#     # 1. Действия при СТАРТЕ приложения
#     # Создаем пул соединений. Ограничиваем размер пула под наши 100 RPS.
#     redis_client = aioredis.from_url(
#         "redis://localhost:6379",
#         decode_responses=True,
#         max_connections=20  # Для 100 RPS этого пула хватит с запасом
#     )
#
#     # Запускаем фоновую задачу прослушивания событий
#     listener_task = asyncio.create_task(redis_event_listener(redis_client))
#     print("Успешно: Подключение к Redis установлено, фоновый очиститель запущен.")
#
#     yield  # В этой точке приложение работает и принимает HTTP-запросы
#
#     # 2. Действия при ОСТАНОВКЕ приложения
#     print("Остановка приложения: завершаем фоновые задачи...")
#     listener_task.cancel()  # Сигнализируем воркеру завершить работу
#     try:
#         await listener_task  # Ожидаем завершения таски
#     except asyncio.CancelledError:
#         pass
#
#     await redis_client.close()  # Закрываем пул соединений с Redis
#     print("Успешно: Соединения с Redis закрыты.")
#
#
# # Инициализируем FastAPI с привязкой нашего lifespan
# app = FastAPI(lifespan=lifespan)

# async def main():
#     redis_client = aioredis.from_url("redis://localhost:6379", decode_responses=True)
#     # Важно: Включаем режим 'Ev' (gEneric + eXpired), чтобы ловить и DEL, и TTL
#     await redis_client.config_set("notify-keyspace-events", "Egx")
#
#     listener_task = asyncio.create_task(redis_event_listener(redis_client))
#     await asyncio.sleep(0.1)
#     DataRegistry = RedisStateManager(redis=redis_client)
#
#     # 1. Заполняем данные
#     await DataRegistry.set_state("Данные 1", channel="quantum0", user=100, command="bite")
#     await DataRegistry.set_state("Данные 2", channel="quantum075", user=100, command="bite")
#     await DataRegistry.set_state("Данные 3", channel="quantum075", user=120, command="bite")
#     await DataRegistry.set_state("Данные 4", channel="quantum075", user=120, command="lick")
#
#     print("База заполнена. Текущее состояние для user=120:")
#     print(await DataRegistry.get_from_redis(user=120))
#     print("-" * 40)
#
#     # 2. ТЕСТ 1: Точечное удаление
#     print("Удаляем точечно: канал 'quantum075', команда 'bite', юзер '120'...")
#     deleted = await DataRegistry.del_state(channel="quantum075", command="bite", user=120)
#     print(f"Удалено записей: {deleted}")
#
#     # Даем микросекунду воркеру почистить индексы и проверяем
#     await asyncio.sleep(0.05)
#     print(f"Оставшиеся данные для user=120: {await DataRegistry.get_from_redis(user=120)}")
#     print("-" * 40)
#
#     # 3. ТЕСТ 2: Удаление по Wildcard
#     print("Удаляем по маске: канал 'tg', команда любая (None), юзер 'user_1'...")
#     # Должно удалиться 2 записи: (tg:start:user_1) и (tg:help:user_1)
#     deleted_wildcard = await DataRegistry.del_state(channel="quantum075", command=None, user=None)
#     print(f"Удалено записей по маске: {deleted_wildcard}")
#
#     deleted_wildcard = await DataRegistry.del_state(channel="quantum0", command=None, user=None)
#     print(f"Удалено записей по маске: {deleted_wildcard}")
#
#     await asyncio.sleep(0.05)
#
#     print(f"Данные в системе после всех удалений: {await DataRegistry.get_from_redis()}")
#
#     await asyncio.sleep(3)
#     listener_task.cancel()
#     await listener_task
#
#
# if __name__ == "__main__":
#     asyncio.run(main())


# TODO:
#   Как сделать так, чтобы сервис не умирал при падении Redis?
