import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from aiomqtt import Client, MqttError, MqttCodeError
from pydantic import BaseModel

from config import settings

logger = logging.getLogger(__name__)


class MQTTClient:
    def __init__(self):
        self._client: Client | None = None
        self._client_id = "twitch-bot"
        self._host = settings.mqtt_host
        self._username = settings.mqtt_username.get_secret_value()
        self._password = settings.mqtt_password.get_secret_value()
        self._prefix = "twibot"

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

    async def publish(self, topic: str, data: dict[str, Any] | BaseModel | str):
        if not self._client:
            # logger.error("MQTT is not initialized", exc_info=True)
            return

        try:
            if isinstance(data, str):
                payload = json.dumps({"message": data})
            elif isinstance(data, dict):
                payload = json.dumps(data, indent=0, ensure_ascii=False)
            elif isinstance(data, BaseModel):
                payload = data.model_dump(mode='json')
            else:
                raise TypeError("Invalid type of payload: %s", type(data))
            await self._client.publish(self._prefix + "/" + topic, payload=json.dumps(data))
        except MqttCodeError:
            logger.error("Cannot publish MQTT message", exc_info=True)
            return

    @staticmethod
    async def __listen(client):
        async for message in client.messages:
            pass
            # print(message.payload)