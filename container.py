import aioboto3
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
from services.stickers import StickersService
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch


class Container(containers.DeclarativeContainer):
    wiring_config = containers.WiringConfiguration(
        modules=[
            "routers.api.admin_api",
            "routers.api.twitch_eventsub",
            "routers.api.user_api",
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
    twitch = providers.Singleton(Twitch)
    mqtt = providers.Singleton(MQTTClient)
    chat_bot = providers.Singleton(
        ChatBot, db_session_factory=db_session_factory, state_manager=state_manager, mqtt=mqtt
    )
    ai = providers.Singleton(OpenAIClient, db_session_factory=db_session_factory)
    sse_manager = providers.Singleton(SSEManager)
    slovotron = providers.Singleton(SlovotronService, db_session_factory=db_session_factory, chat_bot=chat_bot, ssem=sse_manager)
    memealerts = providers.Singleton(MemealertsService, db_session_factory=db_session_factory)  #deprecated!!!
    memealerts_auth = providers.Singleton(MemealertsOAuthService, db_session_factory=db_session_factory)
    memealerts_v2 = providers.Singleton(MemealertsV2Service, db_session_factory=db_session_factory)

    boto_session = providers.Singleton(aioboto3.Session)
    s3 = providers.Singleton(
        FileStorage,
        session=boto_session,
    )

    image_resizer = providers.Factory(ImageResizer)
    stickers_service = providers.Factory(
        StickersService,
        ai=ai, img_resizer=image_resizer, db_session_factory=db_session_factory, s3=s3,
    )
    twitch_eventsub_service = providers.Singleton(
        TwitchEventSubService,
        twitch=twitch,
        chatbot=chat_bot,
        ssem=sse_manager,
        db_session_factory=db_session_factory,
        stickers=stickers_service,
        memealerts=memealerts
    )
