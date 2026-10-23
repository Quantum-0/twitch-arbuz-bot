from uuid import UUID

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from twitchAPI.type import AuthScope

user_scope = [
    AuthScope.MODERATOR_READ_FOLLOWERS,
    AuthScope.CHANNEL_READ_REDEMPTIONS,
    AuthScope.CHANNEL_MANAGE_REDEMPTIONS,
    AuthScope.CHANNEL_READ_SUBSCRIPTIONS,
    AuthScope.MODERATOR_READ_CHATTERS,
    AuthScope.CHANNEL_MANAGE_MODERATORS,
    AuthScope.MODERATION_READ,
    # AuthScope.CHANNEL_BOT
    # user:read:chat?
    AuthScope.USER_READ_BROADCAST,
    AuthScope.USER_EDIT_BROADCAST,
]

# Дополнительные Twitch scope-ы, которых нет в twitchAPI AuthScope enum (4.5.0).
# Используются только в TwitchSettings.login_url (OAuth authorize URL), не передаются в set_user_authentication.
# editor:manage:clips — нужен для Get Clips Download API (GET /helix/clips/downloads).
user_scope_extra: list[str] = [
    "editor:manage:clips",
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
    # Модерация чата (warn / delete message) — выполняется от имени бота-модератора.
    AuthScope.MODERATOR_MANAGE_WARNINGS,
    AuthScope.MODERATOR_MANAGE_CHAT_MESSAGES,
    # Чтение фолловеров канала создателя бота (бот — модератор quantum075).
    AuthScope.MODERATOR_READ_FOLLOWERS,
]
memealerts_scope = [
    "oauth-user-show",
    "oauth-bonus-give",
]


# TODO: use SecretStr
class TwitchSettings(BaseSettings):
    """Twitch: OAuth-приложение, бот, EventSub и Extension."""

    client_id: str
    client_secret: str
    webhook_secret: SecretStr
    # Алиасы — исторические имена энвов без префикса TWITCH_ (файлы .env не меняем).
    login_redirect_url: AnyHttpUrl = Field(validation_alias="LOGIN_REDIRECT_URL")
    reward_redemption_webhook: AnyHttpUrl = Field(validation_alias="REWARD_REDEMPTION_WEBHOOK")
    bot_access_token: str = Field(validation_alias="BOT_ACCESS_TOKEN")
    bot_refresh_token: str = Field(validation_alias="BOT_REFRESH_TOKEN")
    extension_id: str = Field(default="ipeaqzfjtnio3taywpj3ktbink8fy4", validation_alias="EXTENSION_ID")
    extension_secret: SecretStr = Field(validation_alias="EXTENSION_SECRET")
    extension_version: str = Field(default="0.0.1", validation_alias="EXTENSION_VERSION")
    model_config = SettingsConfigDict(env_prefix="TWITCH_", env_file=".env", extra="ignore")

    @property
    def login_url(self) -> str:
        scope: str = "+".join([sp.value for sp in user_scope] + user_scope_extra)
        return (
            f"https://id.twitch.tv/oauth2/authorize?client_id={self.client_id}"
            f"&redirect_uri={self.login_redirect_url}&response_type=code&scope={scope}"
        )


class AppSettings(BaseSettings):
    """Приложение: поведение бота, веб-секреты, observability (без префикса — исторические имена энвов)."""

    update_bot_channels_on_startup: bool = True
    direct_handle_messages: bool = True
    direct_sending_messages: bool = True
    direct_handle_rewards: bool = True
    exception_to_many_unsubscribes: int | None = 20
    # Basic-auth админ-панели GlitchTip/панели модерации.
    admin_api_login: str
    admin_api_password: str
    # Публичный адрес сайта (ссылки в уведомлениях/шаблонах).
    base_url: str = "https://bot.quantum0.ru"
    # Ключ шифрования OAuth-токенов пользователей (utils/cryptography.py).
    fernet_key: SecretStr
    # Секрет сессий Starlette SessionMiddleware.
    middleware_secret_key: str
    # Трейсинг ошибок и производительности.
    sentry_dsn: AnyHttpUrl | None = None
    otel_endpoint: AnyHttpUrl | None = "http://localhost:4317"
    otel_service_name: str = "twitch-bot"
    # Upstream Heat API (клики по экрану, wss) — канал SSE `heat`.
    heat_url: str = "wss://heat-api.j38.net/channel/"
    # Окно перезапуска стрима (мин): если стрим вернулся онлайн в течение этого
    # окна после оффлайна, считается рестартом — уведомление об окончании не
    # отправляется. Равен задержке подтверждения окончания стрима и CD сообщений
    # о рестарте. Одна константа на всю механику, см. docs/telegram.md.
    stream_restart_window_minutes: int = 15
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")


class DatabaseSettings(BaseSettings):
    """PostgreSQL (async + sync движки)."""

    url: str
    sync_url: str
    model_config = SettingsConfigDict(env_prefix="DB_", env_file=".env", extra="ignore")


class RedisSettings(BaseSettings):
    """Redis: кэш и стейт (Cache, StateManager, статистика)."""

    url: str = "redis://localhost:6379"
    model_config = SettingsConfigDict(env_prefix="REDIS_", env_file=".env", extra="ignore")


class MQTTSettings(BaseSettings):
    """MQTT/EMQX pub-sub."""

    host: str = "emqx.msk.quantum0.ru"
    client_id: str = "twitch-bot"
    username: SecretStr
    password: SecretStr
    model_config = SettingsConfigDict(env_prefix="MQTT_", env_file=".env", extra="ignore")


class S3Settings(BaseSettings):
    """S3-хранилище (стикеры, файлы оверлеев)."""

    url: AnyHttpUrl = "http://localhost:9000"
    access_key: SecretStr
    secret_key: SecretStr
    bucket: str = "twibot"
    # Срок жизни публичных ссылок на ИИ-стикеры (дней).
    sticker_expires_days: int = 7
    model_config = SettingsConfigDict(env_prefix="S3_", env_file=".env", extra="ignore")


class AISettings(BaseSettings):
    """ИИ-генерация (провайдер aitunnel, env OPENAI_*; менять на routerai нельзя)."""

    api_key: SecretStr
    base_url: str = "https://api.aitunnel.ru/v1/"
    # Алиасы — исторические имена энвов без префикса OPENAI_ (файлы .env не меняем).
    # 0.37-0.54; 2.5-4 => 0.49 -- 4.42
    cost_multiplier: float = Field(default=1.08, validation_alias="AI_COST_MULTIPLIER")  # 8%
    cost_single_call: float = Field(default=0.1, validation_alias="AI_COST_SINGLE_CALL")  # 10kop
    # Нейронка удаления фона (rembg isnet-anime). Модели gpt-image-2.5-* генерируют
    # прозрачный фон сами (background=transparent), поэтому rembg можно не грузить
    # при старте контейнера: False — не загружать onnx-сессию и не стартовать воркер
    # старого зелёного пайплайна (обводка/тень из альфа-канала работают без нейронки).
    sticker_rembg_enabled: bool = Field(default=False, validation_alias="STICKER_REMBG_ENABLED")
    model_config = SettingsConfigDict(env_prefix="OPENAI_", env_file=".env", extra="ignore")


class MemealertsSettings(BaseSettings):
    """Интеграция Memealerts (OAuth)."""

    redirect_url: AnyHttpUrl = "https://bot.quantum0.ru/memealerts/callback"
    client_id: SecretStr
    client_secret: SecretStr
    state_secret: SecretStr
    model_config = SettingsConfigDict(env_prefix="MEMEALERTS_", env_file=".env", extra="ignore")

    @property
    def oauth_url(self) -> str:
        scope: str = " ".join(sp for sp in memealerts_scope)
        return (
            f"https://memealerts.com/oauth/authorize?client_id={self.client_id.get_secret_value()}"
            f"&redirect_uri={self.redirect_url}&response_type=code&scope={scope}"
            "&state={state}"
        )


class TelegramSettings(BaseSettings):
    """Интеграция с twibot-tg (уведомления) и Telegram Login (OIDC)."""

    bot_username: str = "quantum0s_twitch_bot"
    # Релей twibot-tg (серверные OAuth-запросы идут только через него).
    service_url: str = "http://localhost:8001"
    service_api_key: str = "changeme"
    # Telegram Login (OpenID Connect, core.telegram.org/bots/telegram-login):
    # Client ID/Secret выдаёт @BotFather (Login Widget → Allowed URLs).
    # client_id — числовой id бота. Если не задан — кнопка «Привязать Telegram»
    # в панели скрыта, /auth/telegram редиректит обратно на /panel.
    login_client_id: int | None = None
    login_client_secret: SecretStr = SecretStr("")
    login_redirect_url: AnyHttpUrl = "https://bot.quantum0.ru/auth/telegram/callback"
    model_config = SettingsConfigDict(env_prefix="TELEGRAM_", env_file=".env", extra="ignore")


class TTSSettings(BaseSettings):
    """TTS-сервис (синтез речи)."""

    api_url: str = "http://157.22.205.183:8000/v1/audio/speech"
    api_token: SecretStr = SecretStr("")
    model: str = "neco-arc"
    model_config = SettingsConfigDict(env_prefix="TTS_", env_file=".env", extra="ignore")


class SteamSettings(BaseSettings):
    """Интеграция Steam (OpenID-логин, профиль игрока)."""

    # Steam Web API key (https://steamcommunity.com/dev/apikey)
    api_key: SecretStr = SecretStr("")
    return_to_url: AnyHttpUrl = "https://bot.quantum0.ru/auth/steam/callback"
    model_config = SettingsConfigDict(env_prefix="STEAM_", env_file=".env", extra="ignore")


class SlovotronSettings(BaseSettings):
    """Оверлей Slovotron: секрет для генерации UUIDv3 SSE-каналов."""

    secret: UUID
    model_config = SettingsConfigDict(env_prefix="SLOVOTRON_", env_file=".env", extra="ignore")


class MonitoringSettings(BaseSettings):
    """Мониторинг сервера (страница «Сервер», только модераторы): метрики и алерты."""

    # Каталог /proc для метрик CPU/RAM/load/сеть. По умолчанию — собственный /proc
    # контейнера: в Docker (без lxcfs) /proc/stat, /proc/meminfo и /proc/loadavg
    # показывают хостовые значения, а /proc/net/dev — трафик veth контейнера
    # (трафик сервиса, не всего хоста). Хостовый /proc НЕ монтируется — полный
    # read-only доступ к / и /proc хоста exposing все файлы при RCE отклонён.
    metrics_proc_dir: str = "/proc"
    # Путь на мониторимом разделе хоста для statvfs (диск): пустая директория,
    # забиндленная в compose как ./host-metrics:/host/fs:ro. statvfs возвращает
    # статистику файловой системы — содержимое каталога не читается и не важно.
    metrics_disk_path: str = "/host/fs"
    # Срок хранения агрегированных метрик хоста в БД (дней).
    metrics_retention_days: int = 30
    # Лимиты для алертов (красная плашка на /server + мигающие бейджи в меню).
    # Warning — при достижении 80% лимита, critical — при превышении.
    alert_disk_percent: int = 90
    alert_cpu_percent: int = 90
    alert_mem_percent: int = 90
    # Лимит load average (5 мин); VDS ~2 vCPU, лимит = 2 × ядра.
    alert_load5: float = 4.0
    model_config = SettingsConfigDict(env_prefix="SERVER_", env_file=".env", extra="ignore")


class Settings(BaseSettings):
    twitch: TwitchSettings = Field(default_factory=TwitchSettings)
    app: AppSettings = Field(default_factory=AppSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    mqtt: MQTTSettings = Field(default_factory=MQTTSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    ai: AISettings = Field(default_factory=AISettings)
    memealerts: MemealertsSettings = Field(default_factory=MemealertsSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    steam: SteamSettings = Field(default_factory=SteamSettings)
    slovotron: SlovotronSettings = Field(default_factory=SlovotronSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")


settings = Settings()
