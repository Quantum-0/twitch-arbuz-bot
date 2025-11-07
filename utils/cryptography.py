from cryptography.fernet import Fernet

from config import settings

fernet = Fernet(settings.fernet_key.get_secret_value().encode())


def encrypt_value(value: str | None) -> str | None:
    if value is None:
        return None
    return fernet.encrypt(value.encode()).decode()


def decrypt_value(value: str | None) -> str | None:
    if value is None:
        return None
    return fernet.decrypt(value.encode()).decode()
