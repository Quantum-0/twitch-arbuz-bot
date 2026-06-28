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