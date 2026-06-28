from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints, Field, AnyHttpUrl


class MAUserInfo(BaseModel):
    id: Annotated[str, StringConstraints(pattern="^[0-9a-f]{24}$")] = Field(description="Уникальный неизменяемый идентификатор пользователя.")
    code: str = Field(description="Уникальное имя пользователя (username).")
    name: str = Field(description="Отображаемое и мя пользователя.")
    avatar: AnyHttpUrl | Literal[""] = Field(description="URL аватара пользователя.")
    email: str = Field(description="Email пользователя.")
    # socket_connection_token: str = Field(description="Токен подключения к Centrifugo.")