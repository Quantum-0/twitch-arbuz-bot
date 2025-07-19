from pydantic import SecretStr, AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from twitchAPI.types import AuthScope


user_scope = [AuthScope.CHANNEL_READ_REDEMPTIONS, AuthScope.CHANNEL_MANAGE_REDEMPTIONS]
bot_scope = [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT, AuthScope.CHANNEL_READ_REDEMPTIONS, AuthScope.CHANNEL_MANAGE_REDEMPTIONS]



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

    model_config = SettingsConfigDict(env_prefix='', env_file='.env')

    @property
    def login_twitch_url(self) -> str:
        scope:str = "+".join(sp.value for sp in user_scope)
        url = (
                f"https://id.twitch.tv/oauth2/authorize?client_id={settings.twitch_client_id}"
                f"&redirect_uri={settings.login_redirect_url}&response_type=code&scope={scope}"
        )
        return url

settings = Settings()