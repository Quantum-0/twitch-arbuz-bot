import aioboto3
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dependency_injector import containers, providers

from config import settings
from database.database import AsyncSessionLocal
from services.ai import OpenAIClient
from services.cache import Cache
from services.eventsub_service import TwitchEventSubService
from services.image_resizer import ImageResizer
from services.memes import MemealertsService
from services.memes_v2 import MemealertsOAuthService, MemealertsV2Service
from services.mqtt import MQTTClient
from services.redis_state_manager import RedisStateManager, init_redis
from services.s3 import FileStorage
from services.slovotron import SlovotronService
from services.sse_manager import SSEManager
from services.statistics import StatisticsService
from services.stickers import StickersService
from services.stickers_processor import StickerProcessor
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch


class Container(containers.DeclarativeContainer):
    wiring_config = containers.WiringConfiguration(
        modules=[
            "routers.api.admin_api",
            "routers.api.extension",
            "routers.api.twitch_eventsub",
            "routers.api.user_api",
            "routers.api.user.memealerts",
            "routers.api.user.streamers",
            "routers.api.user.stats",
            "routers.api.slovotron_webhook",
            "routers.web.service_routes",
            "routers.web.memealerts_routes",
            "routers.web.pages",
            "routers.web.overlays",
            "routers.web.file_storage",
            "routers.security_helpers",
            "routers.sse",
        ]
    )

    db_session_factory = providers.Object(AsyncSessionLocal)
    redis = providers.Resource(
        init_redis,
        redis_url=settings.redis_url,
    )
    binary_redis = providers.Resource(
        init_redis,
        binary=True,
        redis_url=settings.redis_url,
    )
    state_manager = providers.Singleton(
        RedisStateManager,
        # redis=redis,
        default_ttl=24 * 60 * 60,
    )
    cache = providers.Singleton(
        Cache,
    )
    statistics = providers.Singleton(
        StatisticsService,
        db_session_factory=db_session_factory,
    )
    twitch = providers.Singleton(Twitch)
    mqtt = providers.Singleton(MQTTClient)
    chat_bot = providers.Singleton(
        ChatBot,
        db_session_factory=db_session_factory,
        state_manager=state_manager,
        mqtt=mqtt,
        statistics=statistics,
    )
    ai = providers.Singleton(OpenAIClient, db_session_factory=db_session_factory, statistics=statistics)
    sse_manager = providers.Singleton(SSEManager, statistics=statistics)
    slovotron = providers.Singleton(
        SlovotronService, db_session_factory=db_session_factory, chat_bot=chat_bot, ssem=sse_manager
    )
    memealerts = providers.Singleton(MemealertsService, db_session_factory=db_session_factory)  # deprecated!!!
    memealerts_auth = providers.Singleton(MemealertsOAuthService, db_session_factory=db_session_factory)
    memealerts_v2 = providers.Singleton(MemealertsV2Service, db_session_factory=db_session_factory)
    stickers_processor = providers.Singleton(StickerProcessor)

    boto_session = providers.Singleton(aioboto3.Session)
    s3 = providers.Singleton(
        FileStorage,
        session=boto_session,
    )

    image_resizer = providers.Factory(ImageResizer)
    stickers_service = providers.Factory(
        StickersService,
        ai=ai,
        img_resizer=image_resizer,
        db_session_factory=db_session_factory,
        s3=s3,
        sticker_processor=stickers_processor,
        statistics=statistics,
    )
    twitch_eventsub_service = providers.Singleton(
        TwitchEventSubService,
        twitch=twitch,
        chatbot=chat_bot,
        ssem=sse_manager,
        db_session_factory=db_session_factory,
        stickers=stickers_service,
        memealerts=memealerts,
        memealerts_v2=memealerts_v2,
        memealerts_auth=memealerts_auth,
        statistics=statistics,
    )

    job_store_factory = providers.Factory(
        SQLAlchemyJobStore,
        url=settings.db_url,
    )

    scheduler = providers.Singleton(
        AsyncIOScheduler,
        job_stores=providers.Dict(
            default=job_store_factory,
        ),
        timezone="UTC",
    )
