# ruff: noqa: S101

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import BadRequestError
from pytest_mock import MockFixture

from services.stickers import (
    CopyrightBlockedError,
    ModerationBlockedException,
    StickersService,
    _extract_api_message,
    _is_moderation_blocked,
    _mentions_protected_ip,
)

# Реальное тело ошибки aitunnel (repro 2026-10-19): причину блока провайдер
# не раскрывает, текст всегда одинаковый.
AITUNNEL_SAFETY_BODY = {
    "error": {
        "message": (
            "Your request was rejected by the safety system. "
            "If you believe this is an error, contact us at help.openai.com "
            "and include the request ID req_c69c8093cf464b26ad5c75ff14b65303."
        ),
        "code": 400,
        "metadata": {"provider_name": "OpenAI"},
    }
}


# No-op переопределение autouse-фикстуры session_override (client_fixtures):
# она тянет db_session -> postgres-контейнер, который этим чистым unit-тестам не нужен.
@pytest.fixture(autouse=True)
def session_override() -> None:
    return None


def make_bad_request(body: dict) -> BadRequestError:
    request = httpx.Request("POST", "https://api.aitunnel.ru/v1/images/generations")
    response = httpx.Response(400, request=request, json=body)
    return BadRequestError(str(body), response=response, body=body)


def make_service(exc: Exception) -> StickersService:
    service = object.__new__(StickersService)
    service._ai = SimpleNamespace(generate_sticker=AsyncMock(side_effect=exc))
    return service


def test_is_moderation_blocked_real_aitunnel_body() -> None:
    assert _is_moderation_blocked(make_bad_request(AITUNNEL_SAFETY_BODY))


def test_is_moderation_blocked_other_bad_request() -> None:
    exc = make_bad_request({"error": {"message": "Invalid image format"}})
    assert not _is_moderation_blocked(exc)


def test_extract_api_message_prefers_nested_error_message() -> None:
    assert _extract_api_message(make_bad_request(AITUNNEL_SAFETY_BODY)) == AITUNNEL_SAFETY_BODY["error"]["message"]


def test_mentions_protected_ip_finds_shrek_in_any_case() -> None:
    assert _mentions_protected_ip("@quantum075 тусит с Шреком и ослом под музыку") == "шрек"
    assert _mentions_protected_ip("party with Shrek and Donkey") == "shrek"


def test_mentions_protected_ip_neutral_prompt() -> None:
    assert _mentions_protected_ip("@quantum075 тусит на дискотеке под молочко") is None


@pytest.mark.parametrize(
    "marker_prompt",
    [
        "`@quantum075 тусит с Шреком и ослом под музыку`",
        "`@quantum075 тусит с Микки Маусом и Гуфи под музыку`",
    ],
)
async def test_generate_sticker_copyright_block(marker_prompt: str, mocker: MockFixture) -> None:
    warning_mock = mocker.patch("services.stickers.logger.warning")
    service = make_service(make_bad_request(AITUNNEL_SAFETY_BODY))
    with pytest.raises(CopyrightBlockedError):
        await service._generate_sticker(marker_prompt, files=[], model="gpt-image-2.5-flare")

    logged = " ".join(str(c) for call in warning_mock.call_args_list for c in call.args)
    assert "blocked by AI provider moderation" in logged
    assert "Your request was rejected by the safety system" in logged
    assert "matched_ip_marker=" in logged


async def test_generate_sticker_plain_moderation_block() -> None:
    service = make_service(make_bad_request(AITUNNEL_SAFETY_BODY))
    with pytest.raises(ModerationBlockedException) as exc_info:
        await service._generate_sticker(
            "@quantum075 тусит на дискотеке под молочко", files=[], model="gpt-image-2.5-flare"
        )
    assert not isinstance(exc_info.value, CopyrightBlockedError)
    assert "авторские права" not in exc_info.value.chatbot_response


def test_copyright_blocked_is_moderation_blocked_subtype() -> None:
    assert issubclass(CopyrightBlockedError, ModerationBlockedException)
    assert "авторские права" in CopyrightBlockedError().chatbot_response
