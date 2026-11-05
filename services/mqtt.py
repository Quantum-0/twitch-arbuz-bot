import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from typing import Any

from aiomqtt import Client, MqttCodeError, MqttError, ProtocolVersion
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import SpanKind
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from pydantic import BaseModel

from config import settings
from utils.observability import spawn_logged_task

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Реконнект к брокеру: экспоненциальный backoff 1..60с.
_RECONNECT_BACKOFF_MAX = 60.0
# Сколько ждать завершения супервизора при остановке приложения.
_SHUTDOWN_TIMEOUT = 15.0


class MQTTClient:
    def __init__(self) -> None:
        self._client: Client | None = None
        self._listen_task: asyncio.Task | None = None
        self._client_id = settings.mqtt.client_id
        self._host = settings.mqtt.host
        self._username = settings.mqtt.username.get_secret_value()
        self._password = settings.mqtt.password.get_secret_value()
        self._prefix = "twibot"

        self._handlers: list[tuple[str, Callable[..., Coroutine[Any, Any, None]]]] = []

    @property
    def is_alive(self) -> bool:
        """Подключение к брокеру установлено и цикл обработки сообщений жив."""
        return self._client is not None and self._listen_task is not None and not self._listen_task.done()

    @asynccontextmanager
    async def lifespan(self):
        # Супервизор держит подключение: при потере связи переподключается с backoff.
        # is_alive=True только когда слушатель жив; между реконнектами publish дропается с логом.
        stop_event = asyncio.Event()
        first_attempt = asyncio.Event()
        supervise_task = asyncio.create_task(self._supervise(stop_event, first_attempt), name="mqtt_supervisor")
        # Как и раньше: входим в lifespan после исхода первой попытки подключения
        # (успех — работаем; неудача — деградированный режим, супервизор ретраит в фоне).
        await first_attempt.wait()
        try:
            yield
        finally:
            stop_event.set()
            try:
                await asyncio.wait_for(supervise_task, timeout=_SHUTDOWN_TIMEOUT)
            except TimeoutError:
                supervise_task.cancel()
            with suppress(asyncio.CancelledError):
                await supervise_task
            self._client = None
            self._listen_task = None

    async def _supervise(self, stop_event: asyncio.Event, first_attempt: asyncio.Event) -> None:
        """Подключение к брокеру с автореконнектом (экспоненциальный backoff 1..60с)."""
        backoff = 1.0
        try:
            while not stop_event.is_set():
                listen_task: asyncio.Task[None] | None = None
                try:
                    async with Client(
                        self._host,
                        username=self._username,
                        password=self._password,
                        identifier=self._client_id,
                        protocol=ProtocolVersion.V5,
                    ) as cli:
                        await cli.subscribe(self._prefix + "/#")
                        self._client = cli
                        listen_task = asyncio.create_task(self.__listen(cli), name="mqtt_listen")
                        self._listen_task = listen_task
                        await self.publish("system", "Bot started")
                        backoff = 1.0
                        first_attempt.set()

                        stopped = asyncio.create_task(stop_event.wait(), name="mqtt_stop_wait")
                        try:
                            await asyncio.wait({listen_task, stopped}, return_when=asyncio.FIRST_COMPLETED)
                        finally:
                            stopped.cancel()

                        if stop_event.is_set():
                            await self.publish("system", "Bot stopped")
                            break
                        # Слушатель умер (MqttError и т.п.) — исключение забираем, чтобы
                        # не получить «Task exception was never retrieved», и реконнектимся.
                        exc = listen_task.exception()
                        if exc is not None:
                            logger.error("MQTT listener died (%r), reconnecting", exc)
                        else:
                            logger.error("MQTT listener finished unexpectedly, reconnecting")
                except MqttError:
                    if stop_event.is_set():
                        break
                    logger.error("MQTT connection lost, reconnect in %.0fs", backoff, exc_info=True)
                    first_attempt.set()
                finally:
                    self._client = None
                    self._listen_task = None
                    if listen_task is not None:
                        listen_task.cancel()
                        if not listen_task.done():
                            with suppress(asyncio.CancelledError):
                                await listen_task
                        elif not listen_task.cancelled():
                            # задача завершилась с исключением (уже залогировано выше) —
                            # забираем его, чтобы не получить «never retrieved»
                            listen_task.exception()

                if stop_event.is_set():
                    break
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX)
        finally:
            # Не даём lifespan зависнуть, если супервизор упал неожиданно.
            first_attempt.set()

    @tracer.start_as_current_span("MQTT: Publish")
    async def publish(self, topic: str, data: dict[str, Any] | BaseModel | str):
        if not self._client:
            logger.error("MQTT publish dropped (no connection): topic=%s", topic)
            return

        try:
            payload: str
            if isinstance(data, str):
                payload = json.dumps({"message": data})
            elif isinstance(data, dict):
                payload = json.dumps(data, indent=0, ensure_ascii=False)
            elif isinstance(data, BaseModel):
                payload = json.dumps(data.model_dump(mode="json"))
            else:
                raise TypeError("Invalid type of payload: %s", type(data))

            carrier: dict[str, str] = {}
            inject(carrier)
            user_properties = [(str(k), str(v)) for k, v in carrier.items()]
            mqtt_properties = Properties(PacketTypes.PUBLISH)
            mqtt_properties.UserProperty = user_properties

            # logger.debug("MQTT Publish CARRIER Headers = %s", carrier)

            await self._client.publish(
                self._prefix + "/" + topic,
                payload=payload,
                properties=mqtt_properties,
            )
        except (MqttCodeError, MqttError):
            logger.error("Cannot publish MQTT message", exc_info=True)
            return

    @staticmethod
    def match_topic(pattern: str, topic: str) -> dict[str, str] | None:
        p_parts = pattern.split("/")
        t_parts = topic.split("/")

        if len(p_parts) != len(t_parts):
            return None

        params = {}

        for i, (p, t) in enumerate(zip(p_parts, t_parts, strict=True)):
            if p == "+":
                params[str(i)] = t
            elif p != t:
                return None

        return params

    def subscribe(
        self,
        topic: str,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ):
        async def wrapped_handler(parent_context: Context, *args, **kwargs):
            with tracer.start_as_current_span(
                f"MQTT: Handle from `{topic}`",
                context=parent_context,  # Связываем с родителем
                kind=SpanKind.CONSUMER,
            ):
                await handler(*args, **kwargs)

        logger.info("Subscribed to topic `%s` with handler `%s`", topic, handler)
        self._handlers.append((topic, wrapped_handler))

    async def __listen(self, client: Client):
        async for message in client.messages:
            try:
                topic = message.topic.value
                payload = json.loads(message.payload.decode())

                if not topic.startswith(self._prefix + "/"):
                    continue

                incoming_headers = {}
                if message.properties and hasattr(message.properties, "UserProperty"):
                    for key, val in message.properties.UserProperty:
                        k = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                        v = val.decode("utf-8") if isinstance(val, bytes) else str(val)
                        incoming_headers[k.lower()] = v  # OTEL ищет 'traceparent' в нижнем регистре

                # logger.debug("MQTT Listen CARRIER Headers = %s", incoming_headers)

                parent_context: Context = extract(incoming_headers)

                short_topic = topic[len(self._prefix) + 1 :]

                for pattern, handler in self._handlers:
                    params = self.match_topic(pattern, short_topic)
                    if params is not None:
                        spawn_logged_task(
                            handler(
                                parent_context,
                                payload,
                            ),
                            name=f"mqtt_handler:{short_topic}",
                        )

            except Exception:
                logger.exception("Failed to handle MQTT message", exc_info=True)
