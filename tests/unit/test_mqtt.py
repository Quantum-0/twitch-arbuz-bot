# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит-тесты MQTT-клиента: матчинг топиков, диспетчер __listen, publish
(сериализация/OTel-свойства/сбои) и lifespan (подключение/деградация)."""

import asyncio
import json
from typing import Any

import pytest
from aiomqtt import MqttCodeError, MqttError
from pydantic import BaseModel

import services.mqtt as mqtt_module
from services.mqtt import MQTTClient


class FakeMessage:
    def __init__(self, topic: str, payload: Any, user_properties: list[tuple[Any, Any]] | None = None):
        self.topic = type("T", (), {"value": topic})()
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        props = None
        if user_properties is not None:
            props = type("P", (), {"UserProperty": user_properties})()
        self.properties = props


class FakeMessages:
    """Очередь сообщений, как у aiomqtt: после исчерпания — ожидание новых.

    ``add`` будит уже висящий итератор (важно для тестов, где сообщения
    добавляются после входа в lifespan).
    """

    def __init__(self, items: list[FakeMessage], hang: bool = True) -> None:
        self._items = list(items)
        self._hang = hang
        self._wake = asyncio.Event()

    def add(self, item: FakeMessage) -> None:
        self._items.append(item)
        self._wake.set()

    def __aiter__(self) -> "FakeMessages":
        return self

    async def __anext__(self) -> FakeMessage:
        while True:
            if self._items:
                return self._items.pop(0)
            if not self._hang:
                raise StopAsyncIteration
            self._wake.clear()
            await self._wake.wait()


class FakeAiomqttClient:
    last: "FakeAiomqttClient | None" = None

    def __init__(
        self,
        host: str,
        connect_error: Exception | None = None,
        listen_error: Exception | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        self.host = host
        self.connect_error = connect_error
        # Ошибка при первом обращении к messages — эмулирует обрыв связи
        # (итератор raises → слушатель умирает с MqttError).
        self.listen_error = listen_error
        self.subscribed: list[str] = []
        self.published: list[dict[str, Any]] = []
        self.closed = False
        self._messages: FakeMessages | None = None
        FakeAiomqttClient.last = self

    def set_messages(self, items: list[FakeMessage], hang: bool = True) -> None:
        if self._messages is None:
            self._messages = FakeMessages(items, hang=hang)
        else:
            # Итератор уже захвачен слушателем — досыпаем в живую очередь
            for item in items:
                self._messages.add(item)
            self._messages._hang = hang

    @property
    def messages(self) -> FakeMessages:
        if self._messages is None:
            if self.listen_error is not None:
                raise self.listen_error
            self._messages = FakeMessages([])
        return self._messages

    async def __aenter__(self) -> "FakeAiomqttClient":
        if self.connect_error:
            raise self.connect_error
        return self

    async def __aexit__(self, *args: Any) -> bool:
        self.closed = True
        return False

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    async def publish(self, topic: str, payload: Any = None, properties: Any = None) -> None:
        self.published.append({"topic": topic, "payload": payload, "properties": properties})


@pytest.fixture
def client() -> MQTTClient:
    return MQTTClient()


class TestMatchTopic:
    def test_exact_match(self, client):
        assert client.match_topic("twitch/message", "twitch/message") == {}

    def test_single_wildcard(self, client):
        assert client.match_topic("twitch/+/message", "twitch/123/message") == {"1": "123"}

    def test_multiple_wildcards(self, client):
        assert client.match_topic("+/+/result", "telegram/abc/result") == {"0": "telegram", "1": "abc"}

    def test_length_mismatch(self, client):
        assert client.match_topic("twitch/+/message", "twitch/123/message/extra") is None
        assert client.match_topic("twitch/+/message", "twitch/message") is None

    def test_literal_mismatch(self, client):
        assert client.match_topic("twitch/+/reward", "twitch/123/message") is None


class TestListen:
    @staticmethod
    async def run_listen(client: MQTTClient, messages: list[FakeMessage]) -> None:
        fake = FakeAiomqttClient("localhost")
        fake.set_messages(messages, hang=False)
        await client._MQTTClient__listen(fake)  # noqa: SLF001
        await asyncio.sleep(0)  # даём созданным задачам хендлеров отработать

    async def test_dispatch_to_matching_handler(self, client):
        received: list[Any] = []

        async def handler(payload):
            received.append(payload)

        client.subscribe("twitch/+/message", handler)
        await self.run_listen(client, [FakeMessage("twibot/twitch/123/message", {"text": "hi"})])

        assert received == [{"text": "hi"}]

    async def test_multiple_handlers_and_wildcards(self, client):
        received_a: list[Any] = []
        received_b: list[Any] = []

        async def handler_a(payload):
            received_a.append(payload)

        async def handler_b(payload):
            received_b.append(payload)

        client.subscribe("telegram/result/+", handler_a)
        client.subscribe("telegram/+/+", handler_b)
        await self.run_listen(
            client,
            [FakeMessage("twibot/telegram/result/abc", {"ok": True})],
        )

        assert received_a == [{"ok": True}]
        assert received_b == [{"ok": True}]

    async def test_no_match_no_call(self, client):
        received: list[Any] = []

        async def handler(payload):
            received.append(payload)

        client.subscribe("slovotron/+/+", handler)
        await self.run_listen(client, [FakeMessage("twibot/twitch/123/message", {"x": 1})])

        assert received == []

    async def test_foreign_prefix_ignored(self, client):
        received: list[Any] = []

        async def handler(payload):
            received.append(payload)

        client.subscribe("+/+/message", handler)
        await self.run_listen(client, [FakeMessage("other/twitch/123/message", {"x": 1})])

        assert received == []

    async def test_invalid_json_does_not_kill_loop(self, client):
        received: list[Any] = []

        async def handler(payload):
            received.append(payload)

        client.subscribe("twitch/+/message", handler)
        await self.run_listen(
            client,
            [
                FakeMessage("twibot/twitch/1/message", b"not-json{"),
                FakeMessage("twibot/twitch/2/message", {"ok": 1}),
            ],
        )

        assert received == [{"ok": 1}]

    async def test_user_property_bytes_headers(self, client):
        received: list[Any] = []

        async def handler(payload):
            received.append(payload)

        client.subscribe("twitch/+/message", handler)
        msg = FakeMessage(
            "twibot/twitch/5/message",
            {"x": 1},
            user_properties=[(b"traceparent", "00-abc-def-01"), ("other", b"value")],
        )
        await self.run_listen(client, [msg])

        assert received == [{"x": 1}]  # headers распакованы без ошибки, хендлер вызван


class TestPublish:
    async def test_no_client_noop(self, client):
        await client.publish("system", "x")  # не падает без подключения

    async def test_str_wrapped_into_message(self, client):
        fake = FakeAiomqttClient("localhost")
        client._client = fake

        await client.publish("system", "Bot started")

        assert len(fake.published) == 1
        pub = fake.published[0]
        assert pub["topic"] == "twibot/system"
        assert json.loads(pub["payload"]) == {"message": "Bot started"}

    async def test_dict_payload_unicode(self, client):
        fake = FakeAiomqttClient("localhost")
        client._client = fake

        await client.publish("telegram/result/x", {"текст": "привет"})

        pub = fake.published[0]
        assert json.loads(pub["payload"]) == {"текст": "привет"}

    async def test_pydantic_payload(self, client):
        fake = FakeAiomqttClient("localhost")
        client._client = fake

        class Payload(BaseModel):
            name: str
            count: int

        await client.publish("x", Payload(name="a", count=2))

        assert json.loads(fake.published[0]["payload"]) == {"name": "a", "count": 2}

    async def test_invalid_type_raises(self, client):
        fake = FakeAiomqttClient("localhost")
        client._client = fake

        with pytest.raises(TypeError):
            await client.publish("x", 123)  # type: ignore[arg-type]

    async def test_mqtt_code_error_swallowed(self, client):
        fake = FakeAiomqttClient("localhost")

        async def boom(*args: Any, **kwargs: Any) -> Any:
            raise MqttCodeError(0x80, "rejected")

        fake.publish = boom  # type: ignore[method-assign]
        client._client = fake

        await client.publish("x", {"a": 1})  # не падает

    async def test_properties_passed(self, client):
        fake = FakeAiomqttClient("localhost")
        client._client = fake

        await client.publish("x", {"a": 1})

        # Properties с UserProperty-списком из OTel-карьеры (может быть пустым)
        props = fake.published[0]["properties"]
        assert props is not None


class TestLifespan:
    async def test_successful_connection(self, client, monkeypatch):
        created: list[FakeAiomqttClient] = []

        def factory(host: str, **kwargs: Any) -> FakeAiomqttClient:
            cli = FakeAiomqttClient(host)
            created.append(cli)
            return cli

        monkeypatch.setattr(mqtt_module, "Client", factory)

        async with client.lifespan():
            fake = created[0]
            assert fake.subscribed == ["twibot/#"]
            assert client.is_alive
            # Bot published на входе
            assert json.loads(fake.published[0]["payload"]) == {"message": "Bot started"}
            # __listen-таска жива (сообщений нет — висит на Event)
            assert client._listen_task is not None

        # Bot stopped на выходе + клиент закрыт
        assert json.loads(fake.published[-1]["payload"]) == {"message": "Bot stopped"}
        assert fake.closed
        assert not client.is_alive
        assert client._client is None
        assert client._listen_task is None

    async def test_listen_task_processes_message(self, client, monkeypatch):
        received: list[Any] = []

        async def handler(payload):
            received.append(payload)

        client.subscribe("twitch/+/message", handler)
        holder: dict[str, FakeAiomqttClient] = {}

        def factory(host: str, **kwargs: Any) -> FakeAiomqttClient:
            cli = FakeAiomqttClient(host)
            holder["cli"] = cli
            return cli

        monkeypatch.setattr(mqtt_module, "Client", factory)

        async with client.lifespan():
            holder["cli"].set_messages([FakeMessage("twibot/twitch/9/message", {"hi": 1})])
            await asyncio.sleep(0.05)
            assert received == [{"hi": 1}]

    async def test_mqtt_error_degrades_gracefully(self, client, monkeypatch):
        def factory(host: str, **kwargs: Any) -> FakeAiomqttClient:
            return FakeAiomqttClient(host, connect_error=MqttError("broker down"))

        monkeypatch.setattr(mqtt_module, "Client", factory)

        # lifespan не падает: работает в деградированном режиме
        async with client.lifespan():
            assert not client.is_alive

        assert not client.is_alive

    async def test_reconnects_after_connection_loss(self, client, monkeypatch):
        """Обрыв связи (слушатель умер с MqttError) → супервизор переподключается."""
        created: list[FakeAiomqttClient] = []

        def factory(host: str, **kwargs: Any) -> FakeAiomqttClient:
            cli = FakeAiomqttClient(host, listen_error=MqttError("connection lost") if not created else None)
            created.append(cli)
            return cli

        monkeypatch.setattr(mqtt_module, "Client", factory)

        async with client.lifespan():
            # Первое подключение умерло → ждём реконнекта (backoff ~1с)
            for _ in range(150):
                if len(created) >= 2 and client.is_alive:
                    break
                await asyncio.sleep(0.02)
            assert len(created) == 2, "супервизор не переподключился"
            assert created[0].closed
            assert created[1].subscribed == ["twibot/#"]
            assert client.is_alive

        assert not client.is_alive
        assert client._client is None
        assert client._listen_task is None
