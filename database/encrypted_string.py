import sqlalchemy as sa
from sqlalchemy.types import TypeDecorator
from utils.cryptography import encrypt_value, decrypt_value


class EncryptedString(TypeDecorator):
    """Кастомный тип данных, который шифрует строку при записи и расшифровывает при чтении."""

    impl = sa.String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Срабатывает при ЗАПИСИ в базу данных (например, INSERT или UPDATE)."""
        if value is not None:
            return encrypt_value(value)
        return value

    def process_result_value(self, value, dialect):
        """Срабатывает при ЧТЕНИИ из базы данных (например, SELECT или RETURNING)."""
        if value is not None:
            return decrypt_value(value)
        return value
