import hashlib
import logging
import pickle
from collections.abc import Callable, Awaitable
from typing import TypeVar

import redis.asyncio as aioredis
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

T = TypeVar("T")


class Cache:
    def __init__(self, default_ttl: int = 4 * 60 * 60):
        # self._r = init_redis()
        self._default_ttl = default_ttl

    async def startup(self, redis: aioredis.Redis, binary_redis: aioredis.Redis):
        self._r = redis
        self._rb = binary_redis

    def _generate_key(self, func: Callable[..., Awaitable[None]], args: tuple, kwargs: dict) -> str:
        """Генерация уникального ключа на основе имени функции и её аргументов."""
        func_name = func.__name__
        # Сериализуем аргументы в байты для создания хеша
        data_to_hash = pickle.dumps((args, kwargs))
        args_hash = hashlib.md5(data_to_hash).hexdigest()
        return f"cache:as_cached:{func_name}:{args_hash}"

    async def as_cached(self, func: Callable[..., Awaitable[T]], *args, cache_ttl: int = 120, **kwargs) -> T:
        """
        Получает значение из кеша или выполняет функцию и сохраняет результат.
        """
        # Генерируем ключ
        cache_key = self._generate_key(func, args, kwargs)

        # Проверяем наличие в Redis
        cached_data = await self._rb.get(cache_key)

        if cached_data:
            # Если данные есть, десериализуем и возвращаем
            logger.info(f"Data for {func.__name__} loaded from cache")
            return pickle.loads(cached_data)

        # Если данных нет, выполняем исходную функцию
        result = await func(*args, **kwargs)

        # Сохраняем результат в Redis (например, с TTL в 3600 секунд)
        await self._rb.setex(cache_key, 3600, pickle.dumps(result))
        # serialized_result = pickle.dumps(result)
        # await self._rb.execute_command("SETEX", cache_key, 3600, serialized_result)
        return result

    async def set_set(self, name: str, values: set[str], ttl: int = 3600, no_error: bool = True) -> None:
        try:
            return await self.__set_set(name, values, ttl)
        except:
            logger.error("Error writing to redis", exc_info=True)
            if not no_error:
                raise

    async def __set_set(self, name: str, values: set[str], ttl: int = 3600) -> None:
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.delete(f'cache:{name}')
            pipe.sadd(f'cache:{name}', *values)
            pipe.expire(f'cache:{name}', ttl)
            await pipe.execute()

    async def get_set(self, name: str, no_error: bool = True) -> set[str]:
        try:
            return await self.__get_set(name)
        except:
            logger.error("Error reading from redis", exc_info=True)
            if not no_error:
                raise
        return set()

    async def __get_set(self, name: str) -> set[str]:
        return await self._r.smembers(f'cache:{name}')

    async def check_rate_limit(self, key: str, limit: int, window_s: int) -> bool:
        key = "rate_limit:" + key
        try:
            count = await self._r.incr(key)
            if count == 1:
                await self._r.expire(key, window_s)
            return count <= limit
        except Exception:
            logger.error("Error checking rate limit in redis", exc_info=True)
            return True



# RESULT of optimization with cache
# Type	Name                # Requests	# Fails	Median (ms)	95%ile (ms)	99%ile (ms)	Average (ms)	Min (ms)	Max (ms)	Average size (bytes)	Current RPS	Current Failures/s
# GET	/profile/quantum075	239	        0	    460	        830	        1100	    502.39	        368	        1828	    27648.66	            0.4	        0
# GET	/streamers	        247	        0	    1200	    1600	    1800	    1235.98	        952	        1861	    179760	                0.7	        0
# GET	/profile/quantum075	1516	    0	    420	        870	        1100	    473.73	        157	        6728	    27647.37	            15.1	    0
# GET	/streamers	        1529	    0	    190	        500	        790	        228.87	        51	        1376	    179661	                14.9	    0