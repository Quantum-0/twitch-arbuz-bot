from datetime import datetime
from typing import Annotated, Literal

from memealerts.types.user_id import UserID
from pydantic import BaseModel, StringConstraints, Field, AnyHttpUrl, NonNegativeInt, NonNegativeFloat


class MAUserInfo(BaseModel):
    id: Annotated[str, StringConstraints(pattern="^[0-9a-f]{24}$")] = Field(description="Уникальный неизменяемый идентификатор пользователя.")
    code: str = Field(description="Уникальное имя пользователя (username).")
    name: str = Field(description="Отображаемое и мя пользователя.")
    avatar: AnyHttpUrl | Literal[""] = Field(description="URL аватара пользователя.")
    email: str = Field(description="Email пользователя.")
    # socket_connection_token: str = Field(description="Токен подключения к Centrifugo.")

class MASupporter(BaseModel):
    supporter_id: UserID = Field(..., alias="supporterId")
    supporter_name: str = Field(..., alias="supporterName")
    supporter_avatar: AnyHttpUrl | None = Field(None, alias="supporterAvatar")
    supporter_link: str | None = Field(None, alias="supporterLink")
    spent: NonNegativeInt
    purchased: NonNegativeInt | NonNegativeFloat
    muted_by_streamer: bool = Field(..., alias="mutedByStreamer")
    joined: datetime | None = Field(None, alias="joined")

class MASupportersList(BaseModel):
    data: list[MASupporter]
    total: int
    # Параметры ответа:
# Поле	Тип	Описание
# data	array	Список саппортеров (см. ниже)
# total	number	Общее количество саппортеров (с учётом query)
# Ресурс саппортера (data[]):
# Поле	Тип	Описание
# supporterId	string	Идентификатор пользователя-саппортера (id пользователя MemeAlerts)
# supporterName	string	Отображаемое имя саппортера
# supporterAvatar	string, null	URL аватара саппортера (CDN)
# supporterLink	string, null	Ссылка на канал саппортера
# spent	number	Сколько мемкоинов саппортер потратил у стримера
# purchased	number	Сколько мемкоинов саппортер приобрёл у стримера
# joined	number	Дата первого взаимодействия (Unix timestamp, мс)
# mutedByStreamer	boolean	Заблокирован ли саппортер стримером