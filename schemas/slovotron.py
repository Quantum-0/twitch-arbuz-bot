from enum import StrEnum
from typing import Literal, Annotated, Union
from uuid import UUID, uuid3

from pydantic import BaseModel, Field

from config import settings


class SlovotronNewGameDataSchema(BaseModel):
    challenge_id: UUID
    secret_word: str


class SlovotronWinDataSchema(BaseModel):
    class SlovotronWinnerSchema(BaseModel):
        login: str
        display_name: str

    winner: SlovotronWinnerSchema
    winning_word: str
    attempts_used: int
    unique_words: int
    repeated_words: int
    round_duration_sec: int


class SlovotronTipDataSchema(BaseModel):
    tip_word: str
    tip_distance: int
    challenge_id: UUID


class SlovotronEvent(StrEnum):
    GAME_NEW = "game-new"
    GAME_TIP = "game-tip"
    GAME_WIN = "game-win"


class SlovotronWebhookBaseSchema(BaseModel):
    channel: str
    secret: UUID

    def validate_secret(self):
        return self.secret == uuid3(namespace=settings.slovotron_secret, name=self.channel)


class SlovotronNewWebhookSchema(SlovotronWebhookBaseSchema):
    event: Literal["game-new"]  # Literal обязателен для дискриминатора
    data: SlovotronNewGameDataSchema


class SlovotronWinWebhookSchema(SlovotronWebhookBaseSchema):
    event: Literal["game-win"]
    data: SlovotronWinDataSchema


class SlovotronTipWebhookSchema(SlovotronWebhookBaseSchema):
    event: Literal["game-tip"]
    data: SlovotronTipDataSchema


SlovotronWebhookSchema = Annotated[
    Union[SlovotronNewWebhookSchema, SlovotronWinWebhookSchema, SlovotronTipWebhookSchema], Field(discriminator="event")
]


# {"channel":"quantum075","event":"game-new","data":{"challenge_id":"c66acc72-258f-4bd5-823d-23fadc70ab6a","secret_word":"медь"}}
#
# {"channel":"quantum075","event":"game-win","data":{"winner":{"login":"quantum075","display_name":"Quantum075"},"winning_word":"медь","attempts_used":2,"unique_words":2,"repeated_words":0,"round_duration_sec":37}}
#
# {"channel":"quantum075","event":"game-tip","data":{"tip_word":"водолаз","tip_distance":150,"challenge_id":"88d617f7-cb77-456f-8871-6f22c642fea9"}}
