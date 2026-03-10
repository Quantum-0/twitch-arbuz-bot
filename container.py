from dependency_injector import containers, providers

from database.database import AsyncSessionLocal
from services.ai import OpenAIClient
from services.eventsub_service import TwitchEventSubService
from services.image_resizer import ImageResizer
from services.mqtt import MQTTClient
from services.sse_manager import SSEManager
from twitch.chat.bot import ChatBot
from twitch.client.twitch import Twitch


class Container(containers.DeclarativeContainer):
    wiring_config = containers.WiringConfiguration(
        modules=[
            "routers.api.admin_api",
            "routers.api.twitch_eventsub",
            "routers.api.user_api",
            "routers.frontend",
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

    image_resizer = providers.Factory(ImageResizer)
    twitch_eventsub_service = providers.Singleton(
        TwitchEventSubService,
        twitch=twitch,
        chatbot=chat_bot,
        ai=ai,
        ssem=sse_manager,
        img_resizer=image_resizer,
        db_session_factory=db_session_factory,
    )
