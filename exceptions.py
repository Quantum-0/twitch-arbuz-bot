from memealerts.types.exceptions import MAError


class UserNotFoundInDatabase(Exception):
    pass


class NotInBetaTest(Exception):
    pass


class ToManyChatUnsubscribesStartupException(Exception):
    """Слишком много пользователей пытаемся отключить от чата. Кажется что-то пошло не так, падаем в ошибку."""


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
