import pytest

from routers.schemas import ChatMessageSchema


@pytest.fixture
def twitch_message_event_raw():
    return {
        "subscription": {
            "id": "0b7f3361-672b-4d39-b307-dd5b576c9b27",
            "status": "enabled",
            "type": "channel.chat.message",
            "version": "1",
            "condition": {
                "broadcaster_user_id": "12826",
                "user_id": "141981764"
            },
            "transport": {
                "method": "webhook",
                "callback": "https://example.com/webhooks/callback"
            },
            "created_at": "2023-11-06T18:11:47.492253549Z",
            "cost": 0
        },
        "event": {
            "broadcaster_user_id": "12826",
            "broadcaster_user_login": "twitch",
            "broadcaster_user_name": "Twitch",
            "chatter_user_id": "141981764",
            "chatter_user_login": "twitchdev",
            "chatter_user_name": "TwitchDev",
            "message_id": "cc106a89-1814-919d-454c-f4f2f970aae7",
            "message": {
                "text": "Hi chat",
                "fragments": [
                    {
                        "type": "text",
                        "text": "Hi chat",
                        "cheermote": None,
                        "emote": None,
                        "mention": None,
                    }
                ]
            },
            "color": "#00FF7F",
            "badges": [
                {
                    "set_id": "moderator",
                    "id": "1",
                    "info": ""
                },
                {
                    "set_id": "subscriber",
                    "id": "12",
                    "info": "16"
                },
                {
                    "set_id": "sub-gifter",
                    "id": "1",
                    "info": ""
                }
            ],
            "message_type": "text",
            "cheer": None,
            "reply": None,
            "channel_points_custom_reward_id": None,
        }
    }

@pytest.fixture
def twitch_message_event_model(twitch_message_event_raw):
    return ChatMessageSchema.model_validate(twitch_message_event_raw)
