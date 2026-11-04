"""Схема ChatMessageWebhookEventSchema: поле emotes не должно ронять входящий payload.

Twitch не присылает emotes в channel.chat.message v1 (см. комментарий в schemas/twitch.py).
Поле типизировано как Any специально: любой неожиданный формат (например, список
{id, begin, end} как в channel.cheer) не должен превращаться в ValidationError
и терять всё сообщение чата.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from schemas.twitch import ChatMessageWebhookEventSchema

PAYLOAD: dict[str, Any] = {
    "broadcaster_user_id": 123456,
    "broadcaster_user_login": "test_user",
    "broadcaster_user_name": "test_user",
    "chatter_user_id": 777,
    "chatter_user_login": "vasya",
    "chatter_user_name": "Vasya",
    "message_id": "cc106a89-1814-919d-454c-f4f2f970aae7",
    "message": {
        "text": "hi",
        "fragments": [{"type": "text", "text": "hi", "cheermote": None, "emote": None, "mention": None}],
    },
    "color": "#00FF7F",
    "badges": [],
    "message_type": "text",
}


def _validate(emotes: Any) -> ChatMessageWebhookEventSchema:
    return ChatMessageWebhookEventSchema.model_validate({**PAYLOAD, "emotes": emotes})


def test_emotes_absent():
    event = ChatMessageWebhookEventSchema.model_validate(PAYLOAD)
    assert event.emotes is None


def test_emotes_dict_format_passthrough():
    event = _validate({"25": [{"start_position": 13, "end_position": 17}]})
    assert event.emotes == {"25": [{"start_position": 13, "end_position": 17}]}


def test_emotes_list_format_does_not_reject_payload():
    # формат из других событий Twitch (channel.cheer и пр.)
    event = _validate([{"id": "81274", "begin": 13, "end": 18}])
    assert event.emotes == [{"id": "81274", "begin": 13, "end": 18}]


def test_emotes_garbage_does_not_reject_payload():
    event = _validate("oops")
    assert event.emotes == "oops"


def test_invalid_payload_still_rejected():
    with pytest.raises(ValidationError):
        ChatMessageWebhookEventSchema.model_validate({**PAYLOAD, "chatter_user_id": "not-an-int"})
