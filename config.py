from pydantic import SecretStr, AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


# TODO: use SecretStr
class Settings(BaseSettings):
    twitch_client_id: str
    twitch_client_secret: str
    login_redirect_url: AnyHttpUrl
    bot_access_token: str
    bot_refresh_token: str
    db_url: str

    model_config = SettingsConfigDict(env_prefix='', env_file='.env')

settings = Settings()
