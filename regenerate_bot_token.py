import asyncio

from twitchAPI.oauth import UserAuthenticator
from twitchAPI.twitch import Twitch

from config import settings, bot_scope


async def generate_bot_tokens():
    twitch = await Twitch(settings.twitch_client_id, settings.twitch_client_secret)

    # Создаем UserAuthenticator с нужными scope
    # target_scope = [s.value for s in bot_scope]  # список строк для scope
    auth = UserAuthenticator(twitch, bot_scope, force_verify=False)

    # Откроет браузер для авторизации
    token, refresh_token = await auth.authenticate()

    print("\nНовые токены бота:")
    print(f"bot_access_token={token}")
    print(f"bot_refresh_token={refresh_token}")

    await twitch.close()

if __name__ == "__main__":
    asyncio.run(generate_bot_tokens())
