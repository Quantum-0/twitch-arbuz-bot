import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, Awaitable

from aiomqtt import Client, MqttError, MqttCodeError
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import inject, extract
from opentelemetry.trace import SpanKind
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from pydantic import BaseModel

from config import settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class MQTTClient:
    def __init__(self):
        self._client: Client | None = None
        self._client_id = settings.mqtt_client_id
        self._host = settings.mqtt_host
        self._username = settings.mqtt_username.get_secret_value()
        self._password = settings.mqtt_password.get_secret_value()
        self._prefix = "twibot"

        self._handlers: list[tuple[str, Callable[..., Awaitable[None]]]] = []

    @asynccontextmanager
    async def lifespan(self):
        try:
            async with Client(self._host, username=self._username, password=self._password, identifier=self._client_id) as cli:
                await cli.subscribe(self._prefix + "/#")
                loop = asyncio.get_event_loop()
                task = loop.create_task(self.__listen(cli))
                self._client = cli
                await self.publish("system", "Bot started")
                yield
                await self.publish("system", "Bot stopped")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        except MqttError as exc:
            logger.error("Couldn't connect MQTT", exc_info=True)
            yield

    @tracer.start_as_current_span("MQTT: Publish")
    async def publish(self, topic: str, data: dict[str, Any] | BaseModel | str):
        if not self._client:
            # logger.error("MQTT is not initialized", exc_info=True)
            return

        try:
            payload: str
            if isinstance(data, str):
                payload = json.dumps({"message": data})
            elif isinstance(data, dict):
                payload = json.dumps(data, indent=0, ensure_ascii=False)
            elif isinstance(data, BaseModel):
                payload = json.dumps(data.model_dump(mode='json'))
            else:
                raise TypeError("Invalid type of payload: %s", type(data))

            carrier = {}
            inject(carrier)
            user_properties = [(str(k), str(v)) for k, v in carrier.items()]
            mqtt_properties = Properties(PacketTypes.PUBLISH)
            mqtt_properties.UserProperty = user_properties

            await self._client.publish(
                self._prefix + "/" + topic,
                payload=payload,
                properties=mqtt_properties
            )
        except MqttCodeError:
            logger.error("Cannot publish MQTT message", exc_info=True)
            return

    @staticmethod
    def match_topic(pattern: str, topic: str) -> dict[str, str] | None:
        p_parts = pattern.split("/")
        t_parts = topic.split("/")

        if len(p_parts) != len(t_parts):
            return None

        params = {}

        for i, (p, t) in enumerate(zip(p_parts, t_parts)):
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
                    kind=SpanKind.CONSUMER
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
                        k = key.decode('utf-8') if isinstance(key, bytes) else str(key)
                        v = val.decode('utf-8') if isinstance(val, bytes) else str(val)
                        incoming_headers[k.lower()] = v  # OTEL ищет 'traceparent' в нижнем регистре

                parent_context: Context = extract(incoming_headers)

                short_topic = topic[len(self._prefix) + 1:]

                for pattern, handler in self._handlers:
                    params = self.match_topic(pattern, short_topic)
                    if params is not None:
                        asyncio.create_task(
                            handler(
                                parent_context,
                                payload,
                            )
                        )

            except Exception:
                logger.exception("Failed to handle MQTT message", exc_info=True)
