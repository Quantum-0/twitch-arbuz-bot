from memealerts.types.exceptions import MAError, MATokenExpiredError, MAUserNotFoundError

__all__ = [
    "UserNotFoundInDatabase",
    "NotInBetaTest",
    "ToManyChatUnsubscribesStartupException",
    "UserBannedError",
    "MADuplicateUserError",
    "MATokenInvalidError",
    "MARefreshTokenError",
    "MATokenRefreshError",
    "MAInvalidTokenError",
    "MANotSupporterError",
    "MAUnavailableError",
    "MAValidationRespError",
    "MANoToken",
    "MAInvalidScopeError",
    "MATokenExpiredError",
    "MAUserNotFoundError",
]


class UserNotFoundInDatabase(Exception):
    pass


class NotInBetaTest(Exception):
    pass


class ToManyChatUnsubscribesStartupException(Exception):
    """Слишком много пользователей пытаемся отключить от чата. Кажется что-то пошло не так, падаем в ошибку."""


class UserBannedError(Exception):
    """Пользователь забанен в сервисе (global-бан с фичей ``site``).

    Выбрасывается из ``user_auth`` / ``user_auth_optional``; обработчик в
    ``main.py`` редиректит HTML-запросы на ``/banned``, API — JSON 403.
    """

    def __init__(self, target_twitch_id: str):
        self.target_twitch_id: str = target_twitch_id
        super().__init__(f"User {target_twitch_id} is banned")


class MADuplicateUserError(MAError):
    def __init__(self, supporter: str):
        self.supporter: str = supporter


class MATokenInvalidError(MAError):
    pass


class MARefreshTokenError(MAError):
    pass


class MATokenRefreshError(MAError):
    """Ошибка токен-эндпоинта MemeAlerts (RFC 6749 §5.2).

    Поле ``error`` — код ошибки из ответа MA: ``invalid_request``,
    ``invalid_client``, ``invalid_grant``, ``unsupported_grant_type``,
    ``server_error`` или ``unknown`` для нестандартных ответов.
    """

    def __init__(self, error: str, description: str = "", status_code: int = 0):
        self.error: str = error
        self.description: str = description
        self.status_code: int = status_code
        super().__init__(f"{error} ({status_code}): {description}" if description else f"{error} ({status_code})")


class MAInvalidTokenError(MAError):
    pass


class MANotSupporterError(MAError):
    """Получатель коинов не является саппортером стримера в MemeAlerts."""


class MAUnavailableError(MAError):
    """
    500 or 502 from API
    """


class MAValidationRespError(MAError):
    """
    Failed to validate response schema from MA.
    """


class MANoToken(MAError):
    """
    У пользователя нет oAuth токена.
    """


class MAInvalidScopeError(MAError):
    """
    Неверный скоуп у пользователя.
    """
