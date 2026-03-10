class UserNotFoundInDatabase(Exception):
    pass


class NotInBetaTest(Exception):
    pass


class ToManyChatUnsubscribesStartupException(Exception):
    """Слишком много пользователей пытаемся отключить от чата. Кажется что-то пошло не так, падаем в ошибку."""
