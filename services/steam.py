"""Steam OpenID-авторизация и получение профиля через Steam Web API.

Флоу:
1. ``build_auth_url`` — формирует URL для редиректа пользователя на Steam OpenID.
2. Steam редиректит обратно на ``return_to`` с параметрами ``openid.*``.
3. ``verify_openid_response`` — POST'ит ``check_authentication`` на
   ``steamcommunity.com/openid/login`` и проверяет ответ ``is_valid:true``.
   При успехе извлекает steamID64 из ``openid.claimed_id``.
4. ``fetch_profile_url`` — дёргает ``ISteamUser/GetPlayerSummaries`` и возвращает
   ``profileurl`` (например, ``https://steamcommunity.com/profiles/<id>/`` или
   ``https://steamcommunity.com/id/<custom>/``).
"""

import logging
from urllib.parse import urlencode, urlparse

import httpx

from config import settings

logger = logging.getLogger(__name__)

STEAM_OPENID_ENDPOINT = "https://steamcommunity.com/openid/login"
STEAM_API_BASE = "https://api.steampowered.com"
# Префикс claimed_id у Steam: https://steamcommunity.com/openid/id/<steamID64>
STEAM_OPENID_ID_PREFIX = "https://steamcommunity.com/openid/id/"


class SteamService:
    """Сервис Steam OpenID-авторизации и подтягивания profileurl."""

    def build_auth_url(self) -> str:
        """Сформировать URL для инициации OpenID-логина через Steam.

        ``openid.return_to`` и ``openid.realm`` берутся из ``settings.steam_return_to_url``.
        Realm — это scheme + host (+ port), без пути (требование OpenID-спеки).
        """
        return_to = str(settings.steam_return_to_url)
        parsed = urlparse(return_to)
        realm = f"{parsed.scheme}://{parsed.netloc}"
        params = {
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "openid.mode": "checkid_setup",
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.return_to": return_to,
            "openid.realm": realm,
        }
        return f"{STEAM_OPENID_ENDPOINT}?{urlencode(params)}"

    async def verify_openid_response(self, params: dict[str, str]) -> str | None:
        """Проверить ответ Steam OpenID через ``check_authentication``.

        :param params: Все ``openid.*`` параметры из query string callback'а.
        :return: steamID64 (строка цифр) при успехе, ``None`` при невалидном ответе.
        """
        verify_params = dict(params)
        verify_params["openid.mode"] = "check_authentication"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    STEAM_OPENID_ENDPOINT,
                    data=verify_params,
                    timeout=10.0,
                )
                response.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.warning("Steam OpenID verification request failed: %s", exc, exc_info=True)
            return None

        body = response.text
        # Steam возвращает plain-text с строкой "is_valid:true" при успехе.
        # Нормализуем: убираем пробелы вокруг двоеточия для надёжной проверки.
        if "is_valid:true" not in body.replace(" ", ""):
            logger.warning("Steam OpenID verification returned is_valid:false: %s", body[:200])
            return None

        claimed_id = params.get("openid.claimed_id")
        if not claimed_id:
            logger.warning("Steam OpenID callback missing openid.claimed_id")
            return None
        # claimed_id имеет вид https://steamcommunity.com/openid/id/76561198073593788
        # Берём последний path-сегмент.
        steam_id = claimed_id.rstrip("/").rsplit("/", 1)[-1]
        if not steam_id.isdigit():
            logger.warning("Steam OpenID claimed_id has unexpected format: %s", claimed_id)
            return None
        return steam_id

    async def fetch_profile_url(self, steam_id: str) -> str | None:
        """Получить ``profileurl`` из ISteamUser/GetPlayerSummaries.

        :param steam_id: steamID64 (например, 76561198073593788).
        :return: profileurl (с завершающим ``/``) или ``None`` при ошибке/пустом ответе.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v0002/",
                    params={
                        "key": settings.steam_api_key.get_secret_value(),
                        "steamids": steam_id,
                    },
                    timeout=10.0,
                )
                response.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.warning("Steam GetPlayerSummaries request failed: %s", exc, exc_info=True)
            return None

        try:
            data = response.json()
        except ValueError:
            logger.warning("Steam GetPlayerSummaries returned non-JSON: %s", response.text[:200])
            return None

        players = data.get("response", {}).get("players", [])
        if not players:
            logger.warning("Steam GetPlayerSummaries returned empty players list for %s", steam_id)
            return None
        profile_url = players[0].get("profileurl")
        if not profile_url:
            logger.warning("Steam GetPlayerSummaries: profileurl missing for %s", steam_id)
            return None
        return profile_url
