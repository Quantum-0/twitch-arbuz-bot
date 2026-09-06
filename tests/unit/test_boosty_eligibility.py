from datetime import datetime, timedelta

from database.models import MemealertsSettings, TwitchUserSettings, User


def build_user(*, age: timedelta, chat_enabled: bool = False) -> User:
    return User(
        created_at=datetime.now() - age,
        overlays_last_usage=None,
        settings=TwitchUserSettings(enable_chat_bot=chat_enabled),
        memealerts=MemealertsSettings(access_token=None),
        tts=None,
    )


def test_new_user_does_not_see_boosty_offer_even_with_enabled_feature() -> None:
    user = build_user(age=timedelta(hours=2), chat_enabled=True)

    assert user.boosty_offer_eligible is False


def test_inactive_existing_user_does_not_see_boosty_offer() -> None:
    user = build_user(age=timedelta(hours=4))

    assert user.boosty_offer_eligible is False


def test_existing_user_with_enabled_feature_sees_boosty_offer() -> None:
    user = build_user(age=timedelta(hours=4), chat_enabled=True)

    assert user.boosty_offer_eligible is True
