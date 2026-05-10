import aioboto3
from dependency_injector import containers, providers

from database.database import AsyncSessionLocal
from services.ai import OpenAIClient
from services.eventsub_service import TwitchEventSubService
from services.image_resizer import ImageResizer
from services.mqtt import MQTTClient
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
            "routers.web.pages",
            "routers.web.overlays",
            "routers.web.file_storage",
            "routers.security_helpers",
            "routers.sse",
            "routers.ws.heat_proxy",
        ]
    )

    db_session_factory = providers.Object(AsyncSessionLocal)
    twitch = providers.Singleton(Twitch)
    chat_bot = providers.Singleton(ChatBot, db_session_factory=db_session_factory)
    ai = providers.Singleton(OpenAIClient, db_session_factory=db_session_factory)
    mqtt = providers.Singleton(MQTTClient)
    sse_manager = providers.Singleton(SSEManager)
    slovotron = providers.Singleton(SlovotronService, db_session_factory=db_session_factory, chat_bot=chat_bot, ssem=sse_manager)

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
    )
