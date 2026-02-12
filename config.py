from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from twitchAPI.type import AuthScope

user_scope = [
    AuthScope.MODERATOR_READ_FOLLOWERS,
    AuthScope.CHANNEL_READ_REDEMPTIONS,
    AuthScope.CHANNEL_MANAGE_REDEMPTIONS,
    AuthScope.MODERATOR_READ_CHATTERS,
    AuthScope.CHANNEL_MANAGE_MODERATORS,
    AuthScope.MODERATION_READ,
    # AuthScope.CHANNEL_BOT
    # user:read:chat?
]
bot_scope = [
    # AuthScope.USER_CHAT_READ,
    AuthScope.CHAT_READ,
    AuthScope.CHAT_EDIT,
    AuthScope.CHANNEL_READ_REDEMPTIONS,
    AuthScope.CHANNEL_MANAGE_REDEMPTIONS,
    AuthScope.MODERATOR_MANAGE_SHOUTOUTS,
    AuthScope.USER_BOT,
    AuthScope.USER_WRITE_CHAT,
    AuthScope.USER_READ_CHAT,
    AuthScope.CHANNEL_BOT,
]


# TODO: use SecretStr
class Settings(BaseSettings):
    twitch_client_id: str
    twitch_client_secret: str
    login_redirect_url: AnyHttpUrl
    reward_redemption_webhook: AnyHttpUrl
    twitch_webhook_secret: SecretStr
    bot_access_token: str
    bot_refresh_token: str
    db_url: str
    db_sync_url: str
    fernet_key: SecretStr
    sentry_dsn: AnyHttpUrl | None = None
    admin_api_login: str
    admin_api_password: str
    update_bot_channels_on_startup: bool = True
    middleware_secret_key: str
    heat_url: str = "wss://heat-api.j38.net/channel/"
    # github_token: SecretStr
    # github_repo_owner: str = "Quantum-0"
    # github_repo_name: str
    openai_api_key: SecretStr
    openai_base_url: str = "https://api.aitunnel.ru/v1/"
    mqtt_host: str = "emqx.msk.quantum0.ru"
    mqtt_client_id: str = "twitch-bot"
    mqtt_username: SecretStr
    mqtt_password: SecretStr
    direct_handle_messages: bool = True
    direct_handle_rewards: bool = True

    model_config = SettingsConfigDict(env_prefix="", env_file=".env")

    @property
    def login_twitch_url(self) -> str:
        scope: str = "+".join(sp.value for sp in user_scope)
        url = (
            f"https://id.twitch.tv/oauth2/authorize?client_id={settings.twitch_client_id}"
            f"&redirect_uri={settings.login_redirect_url}&response_type=code&scope={scope}"
        )
        return url


settings = Settings()
