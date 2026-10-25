# ruff: noqa: S101, S105, S106, S107, ARG001, ARG002, ARG005, PT011, SLF001

"""Юнит- и DB-тесты StickersService (services/stickers.py).

Модерационные ветки _generate_sticker покрыты в test_stickers_moderation.py —
здесь остальные пути: гейты референсов, сборка промпта, кэш, S3/DB-сохранение.
"""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from openai import APIStatusError, BadRequestError

from config import settings
from database.models import CharacterInfo, GeneratedImage, TwitchUserSettings, User
from schemas.enums import AIReferenceUsagePolicy
from services.s3 import FileNotExistError
from services.stickers import (
    AIProviderBudgetExceededError,
    CharacterNotFoundError,
    ForeignReferenceNotAllowedError,
    NegativeBalanceError,
    OpenAIAPIStatusErrorException,
    OpenAIBadRequestException,
    OwnCharacterRequiredError,
    StickersService,
    UnknownRedemptionProcessingException,
)


class SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return False


class _FakeBegin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class SessionWithFakeBegin:
    """Прокси над AsyncSession: begin() — пустой контекст (сервис-код внутри
    `async with session.begin()` сам делает commit)."""

    def __init__(self, session):
        self._session = session

    def begin(self):
        return _FakeBegin()

    def __getattr__(self, name):
        return getattr(self._session, name)


class SessionCtxNoBegin(SessionCtx):
    """db_session-фикстура уже в транзакции: session.begin() кидает InvalidRequestError.
    Подменяем begin() пустым контекстом — коммит делает сам код сервиса."""

    async def __aenter__(self):
        return SessionWithFakeBegin(self.session)


class FakeStatistics:
    def __init__(self):
        self.timing = []

    def inc_timing(self, type_, subtype="", value_ms=0, **kwargs):
        self.timing.append((str(type_), subtype, value_ms))


def fake_ai(result=(b"img-bytes", 0.5)):
    return SimpleNamespace(
        generate_sticker=AsyncMock(return_value=result),
        generate_sticker_by_refs=AsyncMock(return_value=result),
    )


def fake_s3(get_error=None):
    s3 = Mock()
    if get_error is not None:
        s3.get_object = AsyncMock(side_effect=get_error)
    else:
        s3.get_object = AsyncMock(return_value=b"ref-png")
    s3.put_object = AsyncMock()
    return s3


def make_service(
    ai=None,
    s3=None,
    db=None,
    statistics=None,
    processor=None,
):
    return StickersService(
        ai=ai or fake_ai(),
        img_resizer=SimpleNamespace(resize=AsyncMock(side_effect=lambda b: b)),
        db_session_factory=db or (lambda: None),
        s3=s3 or fake_s3(),
        sticker_processor=processor or Mock(outline=AsyncMock(side_effect=lambda b: b)),
        statistics=statistics,
    )


def make_channel(
    login="test_user",
    policy=AIReferenceUsagePolicy.ALLOW,
    own_character=False,
    model="flare",
    balance="10",
    twitch_id="123456",
    user_id=1,
):
    return SimpleNamespace(
        id=user_id,
        twitch_id=twitch_id,
        login_name=login,
        balance=Decimal(balance),
        settings=SimpleNamespace(
            ai_reference_usage_policy=policy,
            ai_require_own_character=own_character,
            ai_sticker_model=model,
        ),
    )


def make_api_error(cls, status, body=None):
    request = httpx.Request("POST", "https://api.aitunnel.ru/v1/images/generations")
    response = httpx.Response(status, request=request, json=body)
    return cls(str(body or "error"), response=response, body=body)


# ── _generate_sticker: не-модерационные ветки ─────────────────────────────


async def test_generate_sticker_with_refs_uses_by_refs_call():
    ai = fake_ai()
    service = make_service(ai=ai)

    image, cost = await service._generate_sticker(
        "prompt", files=[b"ref"], model="gpt-image-2.5-flare", background="transparent"
    )

    assert image == b"img-bytes"
    assert cost == 0.5
    ai.generate_sticker_by_refs.assert_awaited_once_with(
        prompt="prompt", refs=[b"ref"], model="gpt-image-2.5-flare", background="transparent"
    )
    ai.generate_sticker.assert_not_awaited()


async def test_generate_sticker_without_files_uses_plain_call():
    ai = fake_ai()
    service = make_service(ai=ai)

    await service._generate_sticker("prompt", files=[], model="gpt-image-1-mini", background=None)

    ai.generate_sticker.assert_awaited_once_with(prompt="prompt", model="gpt-image-1-mini", background=None)
    ai.generate_sticker_by_refs.assert_not_awaited()


async def test_generate_sticker_402_maps_to_provider_budget():
    ai = fake_ai()
    ai.generate_sticker.side_effect = make_api_error(APIStatusError, 402, {"message": "insufficient quota"})
    service = make_service(ai=ai)

    with pytest.raises(AIProviderBudgetExceededError):
        await service._generate_sticker("prompt", files=[], model="m")


async def test_generate_sticker_other_api_status_error():
    ai = fake_ai()
    ai.generate_sticker.side_effect = make_api_error(APIStatusError, 500, {"message": "upstream boom"})
    service = make_service(ai=ai)

    with pytest.raises(OpenAIAPIStatusErrorException):
        await service._generate_sticker("prompt", files=[], model="m")


async def test_generate_sticker_non_moderation_bad_request():
    ai = fake_ai()
    ai.generate_sticker.side_effect = make_api_error(BadRequestError, 400, {"error": {"message": "bad size"}})
    service = make_service(ai=ai)

    with pytest.raises(OpenAIBadRequestException):
        await service._generate_sticker("prompt", files=[], model="m")


async def test_generate_sticker_unknown_exception():
    ai = fake_ai()
    ai.generate_sticker.side_effect = RuntimeError("unexpected")
    service = make_service(ai=ai)

    with pytest.raises(UnknownRedemptionProcessingException):
        await service._generate_sticker("prompt", files=[], model="m")


# ── _prepare_final_prompt / _check_missing_references ─────────────────────


async def test_prepare_final_prompt_mini_with_files_and_chars():
    service = make_service()

    result = await service._prepare_final_prompt(
        "котик танцует", characters={"Alise": "рыжая кошка"}, with_files=True, transparent_background=True
    )

    assert "`котик танцует`" in result
    assert "transparent background and the white outline like a sticker" in result
    assert "Character descriptions:" in result
    assert "*@Alise*: рыжая кошка" in result
    assert "The appearance of the characters in the attached files" in result


async def test_prepare_final_prompt_mini_without_files():
    service = make_service()

    result = await service._prepare_final_prompt("котик", characters={}, with_files=False, transparent_background=True)

    assert "transparent background and the white outline like a sticker" in result
    assert "attached files" not in result


async def test_prepare_final_prompt_full_model_with_files():
    service = make_service()

    result = await service._prepare_final_prompt(
        "котик", characters={"Alise": "рыжая кошка"}, with_files=True, transparent_background=False
    )

    assert "[Character Definitions]" in result
    assert "[Character Rule]" in result
    assert "[Composition Rule]" in result
    assert "[Background Rule]" in result
    assert "@Alise:\nрыжая кошка" in result
    assert "alpha = 0" in result


async def test_prepare_final_prompt_full_model_without_files_no_chars():
    service = make_service()

    result = await service._prepare_final_prompt("котик", characters={}, with_files=False, transparent_background=False)

    assert "[Character Definitions]" not in result
    assert "[Character Rule]" not in result
    assert "[Background Rule]" in result


def test_check_missing_references_all_found():
    char = SimpleNamespace(name="alise")
    rows = [(char, "alise", True)]
    StickersService._check_missing_references({"Alise"}, {"alise"}, rows)


def test_check_missing_references_raises_with_original_case():
    char = SimpleNamespace(name="alise")
    rows = [(char, "alise", True)]

    with pytest.raises(CharacterNotFoundError) as exc_info:
        StickersService._check_missing_references({"Alise", "Ghost"}, {"alise", "ghost"}, rows)

    assert exc_info.value.chatbot_response == "Не найден референс персонажа для: @Ghost. Баллы возвращены!"


def test_mention_regex_matches_logins_with_underscore():
    assert set(re.findall(r"@(\w+)", "танцует с @test_user и @Alise")) == {"test_user", "Alise"}


# ── build_sticker: гейты и маршрутизация моделей ──────────────────────────


async def test_build_sticker_requires_own_character(monkeypatch):
    service = make_service()
    monkeypatch.setattr(service, "_handle_refs_from_prompt", AsyncMock(return_value=({}, [])))

    channel = make_channel(own_character=True)
    with pytest.raises(OwnCharacterRequiredError):
        await service.build_sticker(channel, "котик без персонажа", "chatter")


async def test_build_sticker_own_character_gate_passes_when_mentioned(monkeypatch):
    service = make_service()
    monkeypatch.setattr(service, "_handle_refs_from_prompt", AsyncMock(return_value=({}, [])))
    monkeypatch.setattr(service, "_save_to_s3", AsyncMock())
    monkeypatch.setattr(service, "_save_to_db", AsyncMock())

    channel = make_channel(own_character=True)
    file_id = await service.build_sticker(channel, "веселье с @test_user", "chatter")

    assert isinstance(file_id, UUID)


async def test_build_sticker_prompt_without_mentions_uses_cache(monkeypatch):
    service = make_service()
    cached_id = uuid4()
    monkeypatch.setattr(service, "_get_cached_by_prompt", AsyncMock(return_value=cached_id))

    file_id = await service.build_sticker(make_channel(), "котик танцует", "chatter")

    assert file_id == cached_id


async def test_build_sticker_prompt_with_mentions_skips_cache(monkeypatch):
    service = make_service()
    get_cached = AsyncMock(return_value=uuid4())
    monkeypatch.setattr(service, "_get_cached_by_prompt", get_cached)
    monkeypatch.setattr(service, "_handle_refs_from_prompt", AsyncMock(return_value=({}, [])))
    monkeypatch.setattr(service, "_save_to_s3", AsyncMock())
    monkeypatch.setattr(service, "_save_to_db", AsyncMock())

    file_id = await service.build_sticker(make_channel(), "танец с @Alise", "chatter")

    get_cached.assert_not_awaited()
    assert isinstance(file_id, UUID)


async def test_build_sticker_negative_balance():
    service = make_service()

    # @-упоминание пропускает кэш-лукап — до обращения к БД доходит проверка баланса
    with pytest.raises(NegativeBalanceError):
        await service.build_sticker(make_channel(balance="0"), "танец @котика", "chatter")


async def test_build_sticker_legacy_model_falls_back_to_flare(monkeypatch):
    ai = fake_ai()
    service = make_service(ai=ai)
    monkeypatch.setattr(service, "_get_cached_by_prompt", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_handle_refs_from_prompt", AsyncMock(return_value=({}, [])))
    monkeypatch.setattr(service, "_save_to_s3", AsyncMock())
    monkeypatch.setattr(service, "_save_to_db", AsyncMock())

    file_id = await service.build_sticker(make_channel(model="quality"), "котик", "chatter")

    assert isinstance(file_id, UUID)
    # Легаси-значение → flare (gpt-image-2.5-flare), полный пайплайн с пост-обработкой
    call = ai.generate_sticker.await_args
    assert call.kwargs["model"] == "gpt-image-2.5-flare"
    assert call.kwargs["background"] == "transparent"


async def test_build_sticker_mini_model_skips_postprocessing(monkeypatch):
    ai = fake_ai()
    processor = Mock(outline=AsyncMock(side_effect=lambda b: b"outlined"))
    statistics = FakeStatistics()
    service = make_service(ai=ai, processor=processor, statistics=statistics)
    monkeypatch.setattr(service, "_get_cached_by_prompt", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_handle_refs_from_prompt", AsyncMock(return_value=({}, [])))
    monkeypatch.setattr(service, "_save_to_s3", AsyncMock())
    monkeypatch.setattr(service, "_save_to_db", AsyncMock())

    await service.build_sticker(make_channel(model="mini"), "котик", "chatter")

    ai.generate_sticker.assert_awaited_once()
    assert ai.generate_sticker.await_args.kwargs["model"] == "gpt-image-1-mini"
    assert ai.generate_sticker.await_args.kwargs["background"] is None
    processor.outline.assert_not_called()
    assert statistics.timing == []  # mini не пишет timing пост-обработки


async def test_build_sticker_full_model_outlines_and_records_timing(monkeypatch):
    ai = fake_ai()
    processor = Mock(outline=AsyncMock(side_effect=lambda b: b"outlined"))
    statistics = FakeStatistics()
    s3 = fake_s3()
    service = make_service(ai=ai, s3=s3, processor=processor, statistics=statistics)
    monkeypatch.setattr(service, "_get_cached_by_prompt", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_handle_refs_from_prompt", AsyncMock(return_value=({}, [])))
    save_db = AsyncMock()
    monkeypatch.setattr(service, "_save_to_db", save_db)

    file_id = await service.build_sticker(make_channel(), "котик", "chatter")

    processor.outline.assert_awaited_once()
    assert len(statistics.timing) == 1
    assert statistics.timing[0][1] == "post_processing"
    s3.put_object.assert_awaited_once()
    assert s3.put_object.await_args.args[0] == f"ai-gen-stickers/{file_id}.png"
    assert s3.put_object.await_args.args[1] == b"outlined"
    save_db.assert_awaited_once()
    saved_args = save_db.await_args.args
    assert saved_args[1] == "котик"
    assert saved_args[2] == "chatter"
    assert saved_args[4] == 0.5


# ── _handle_refs_from_prompt: гейты на реальной БД ────────────────────────


async def add_character(db_session, name, description=None, file_id=None, approved=True):
    char = CharacterInfo(name=name, description=description, file_id=file_id, approved=approved)
    db_session.add(char)
    await db_session.flush()
    return char


async def add_user_with_settings(db_session, login, twitch_id, allow_on_other_channels=True):
    """Строка TwitchUserSettings создаётся after_insert-ивентом User — не дублируем её,
    иначе JOIN в _handle_refs даёт fanout-дубли строк персонажа."""
    user = User(
        twitch_id=twitch_id,
        login_name=login,
        profile_image_url=f"https://example.com/{login}.png",
        access_token=f"{login}-acc",
        refresh_token=f"{login}-ref",
    )
    db_session.add(user)
    await db_session.flush()
    settings = (
        await db_session.execute(sa.select(TwitchUserSettings).where(TwitchUserSettings.user_id == user.id))
    ).scalar_one()
    settings.ai_reference_allow_on_other_channels = allow_on_other_channels
    await db_session.flush()
    return user


async def test_refs_no_mentions_returns_empty(db_session, test_user):
    service = make_service(db=lambda: SessionCtx(db_session))

    descriptions, refs = await service._handle_refs_from_prompt("просто котик", test_user)

    assert descriptions == {}
    assert refs == []


async def test_refs_own_character_always_allowed(db_session, test_user):
    await add_character(db_session, "test_user", description="хтонический стример")
    service = make_service(db=lambda: SessionCtx(db_session))
    channel = make_channel(policy=AIReferenceUsagePolicy.DENY)

    descriptions, refs = await service._handle_refs_from_prompt("танцует @test_user", channel)

    assert descriptions == {"test_user": "хтонический стример"}
    assert refs == []


async def test_refs_policy_deny_blocks_foreign(db_session, test_user):
    await add_character(db_session, "guest", description="гость")
    service = make_service(db=lambda: SessionCtx(db_session))
    channel = make_channel(policy=AIReferenceUsagePolicy.DENY)

    with pytest.raises(ForeignReferenceNotAllowedError) as exc_info:
        await service._handle_refs_from_prompt("танцует с @guest", channel)

    assert "запретил" in exc_info.value.chatbot_response


async def test_refs_with_my_character_requires_channel_mention(db_session, test_user):
    await add_character(db_session, "guest")
    service = make_service(db=lambda: SessionCtx(db_session))
    channel = make_channel(policy=AIReferenceUsagePolicy.WITH_MY_CHARACTER)

    with pytest.raises(ForeignReferenceNotAllowedError):
        await service._handle_refs_from_prompt("танцует с @guest", channel)


async def test_refs_with_my_character_passes_when_channel_mentioned(db_session, test_user):
    await add_character(db_session, "guest", description="гость")
    await add_character(db_session, "test_user", description="стример")
    service = make_service(db=lambda: SessionCtx(db_session))
    channel = make_channel(policy=AIReferenceUsagePolicy.WITH_MY_CHARACTER)

    descriptions, refs = await service._handle_refs_from_prompt("танцуют с @guest и @test_user", channel)

    assert set(descriptions) == {"guest", "test_user"}
    assert refs == []


async def test_refs_owner_veto_blocks_foreign_character(db_session, test_user):
    owner = await add_user_with_settings(db_session, "other_streamer", "42", allow_on_other_channels=False)
    assert owner.id is not None
    await add_character(db_session, "other_streamer")
    service = make_service(db=lambda: SessionCtx(db_session))
    channel = make_channel(policy=AIReferenceUsagePolicy.ALLOW)

    with pytest.raises(ForeignReferenceNotAllowedError) as exc_info:
        await service._handle_refs_from_prompt("вечеринка с @other_streamer", channel)

    assert "@other_streamer" in exc_info.value.chatbot_response


async def test_refs_owner_allowed_permits_foreign_character(db_session, test_user):
    await add_user_with_settings(db_session, "other_streamer", "42", allow_on_other_channels=True)
    file_id = uuid4()
    await add_character(db_session, "other_streamer", description="друг", file_id=file_id)
    s3 = fake_s3()
    service = make_service(s3=s3, db=lambda: SessionCtx(db_session))
    channel = make_channel(policy=AIReferenceUsagePolicy.ALLOW)

    descriptions, refs = await service._handle_refs_from_prompt("вечеринка с @other_streamer", channel)

    assert descriptions == {"other_streamer": "друг"}
    assert refs == [b"ref-png"]
    s3.get_object.assert_awaited_once_with(f"refs/{file_id}.png")


async def test_refs_custom_reference_without_owner_allowed(db_session, test_user):
    """Персонаж без маппинга на User (кастомный референс) — гейт B не применяется."""
    await add_character(db_session, "fantasy_hero", description="рыцарь")
    service = make_service(db=lambda: SessionCtx(db_session))
    channel = make_channel(policy=AIReferenceUsagePolicy.ALLOW)

    descriptions, _ = await service._handle_refs_from_prompt("подвиги @fantasy_hero", channel)

    assert descriptions == {"fantasy_hero": "рыцарь"}


async def test_refs_unapproved_or_unknown_raises_not_found(db_session, test_user):
    await add_character(db_session, "ghost", approved=False)
    service = make_service(db=lambda: SessionCtx(db_session))
    channel = make_channel()

    with pytest.raises(CharacterNotFoundError) as exc_info:
        await service._handle_refs_from_prompt("ночной стиль: @Ghost и @Nobody", channel)

    assert "@Ghost" in exc_info.value.chatbot_response
    assert "@Nobody" in exc_info.value.chatbot_response


# ── Реальная БД: кэш, сохранение, выдача ──────────────────────────────────


async def add_generated_image(db_session, channel_twitch_id, prompt, *, shown_at=None, created_at=None):
    image = GeneratedImage(
        prompt=prompt,
        by_chatter="chatter",
        on_channel=int(channel_twitch_id),
        cost=0.5,
        file_id=uuid4(),
        shown_at=shown_at,
        created_at=created_at or datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(image)
    await db_session.commit()
    return image


async def fetch_image(db_session, file_id):
    return (await db_session.execute(sa.select(GeneratedImage).where(GeneratedImage.file_id == file_id))).scalar_one()


async def test_get_cached_by_prompt_hit_case_insensitive(db_session, test_user):
    image = await add_generated_image(db_session, test_user.twitch_id, "КОТИК Танцует")
    service = make_service(db=lambda: SessionCtx(db_session))

    cached = await service._get_cached_by_prompt("котик танцует")

    assert cached == image.file_id


async def test_get_cached_by_prompt_expired_after_7_days(db_session, test_user):
    old_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=8)
    await add_generated_image(db_session, test_user.twitch_id, "котик", created_at=old_date)
    service = make_service(db=lambda: SessionCtx(db_session))

    cached = await service._get_cached_by_prompt("котик")

    assert cached is None


async def test_save_to_db_increments_total_spent(db_session, test_user):
    service = make_service(db=lambda: SessionCtxNoBegin(db_session))
    file_id = uuid4()
    cost = 0.5

    await service._save_to_db(file_id, "котик", "chatter", test_user, cost)
    await db_session.refresh(test_user, attribute_names=["total_spent"])

    # Numeric(12,2) в БД округляет float-артефакты произведения
    raw_delta = Decimal(cost * settings.ai.cost_multiplier + settings.ai.cost_single_call)
    assert test_user.total_spent == raw_delta.quantize(Decimal("0.01"))
    row = await fetch_image(db_session, file_id)
    assert row.on_channel == int(test_user.twitch_id)
    assert row.by_chatter == "chatter"


async def test_get_unshown_returns_latest(db_session, test_user):
    await add_generated_image(db_session, test_user.twitch_id, "первый")
    newer = await add_generated_image(db_session, test_user.twitch_id, "второй")
    service = make_service(db=lambda: SessionCtx(db_session))

    unshown = await service.get_unshown(int(test_user.twitch_id))

    assert unshown == newer.file_id


async def test_get_unshown_empty_when_all_shown(db_session, test_user):
    await add_generated_image(
        db_session, test_user.twitch_id, "показан", shown_at=datetime.now(UTC).replace(tzinfo=None)
    )
    service = make_service(db=lambda: SessionCtx(db_session))

    assert await service.get_unshown(int(test_user.twitch_id)) is None


async def test_get_sticker_marks_viewed(db_session, test_user):
    image = await add_generated_image(db_session, test_user.twitch_id, "котик")
    s3 = fake_s3()
    service = make_service(s3=s3, db=lambda: SessionCtx(db_session))

    data = await service.get_sticker(image.file_id, mark_as_viewed=True)

    assert data == b"ref-png"
    row = await fetch_image(db_session, image.file_id)
    assert row.shown_at is not None


async def test_get_sticker_missing_file_returns_none(db_session):
    service = make_service(s3=fake_s3(get_error=FileNotExistError("missing")))

    assert await service.get_sticker(uuid4()) is None


# ── Интеграционный сценарий build_sticker на реальной БД ──────────────────


async def test_build_sticker_end_to_end_with_db(db_session, test_user):
    await add_character(db_session, "test_user", description="стример")
    # Пополняем баланс: balance — computed (total_deposited - total_spent), по умолчанию 0
    test_user.total_deposited = Decimal("100")
    await db_session.commit()
    await db_session.refresh(test_user, attribute_names=["balance", "total_spent"])
    ai = fake_ai()
    s3 = fake_s3()
    processor = Mock(outline=AsyncMock(side_effect=lambda b: b"outlined"))
    service = make_service(ai=ai, s3=s3, processor=processor, statistics=FakeStatistics())
    service._db_session_factory = lambda: SessionCtxNoBegin(db_session)

    file_id = await service.build_sticker(test_user, "танцует как @Test_User", "chatter")

    assert isinstance(file_id, UUID)
    s3.put_object.assert_awaited_once_with(f"ai-gen-stickers/{file_id}.png", b"outlined")
    row = await fetch_image(db_session, file_id)
    assert row.prompt == "танцует как @Test_User"
    await db_session.refresh(test_user, attribute_names=["total_spent"])
    assert test_user.total_spent > 0
    # В финальном промпте — описание персонажа из БД
    assert "стример" in ai.generate_sticker.await_args.kwargs["prompt"]
