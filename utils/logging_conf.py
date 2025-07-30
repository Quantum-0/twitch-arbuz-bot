import logging
import logging.config

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,  # сохраняем стандартные логгеры uvicorn
    "formatters": {
        "default": {
            "format": "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "uvicorn": {
            "format": "%(levelprefix)s %(message)s",
            "()": "uvicorn.logging.DefaultFormatter",
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
        "uvicorn": {
            "formatter": "uvicorn",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
    },
    "root": {  # корневой логгер
        "level": "DEBUG",
        "handlers": ["default"],
    },
    "loggers": {
        "uvicorn": {"handlers": ["uvicorn"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO", "propagate": True},
        "uvicorn.access": {"handlers": ["uvicorn"], "level": "INFO", "propagate": False},


        "twitchAPI": {"level": "INFO", "propagate": False},
        "python_multipart.multipart": {"level": "WARN", "propagate": False},
        "asyncio": {"level": "WARN", "propagate": False},
        "urllib3.connectionpool": {"level": "WARN", "propagate": False},
        "httpcore.connection": {"level": "INFO", "propagate": False},
        "httpcore.http11": {"level": "INFO", "propagate": False},
        # "twitch.bot": {"level": "DEBUG", "propagate": False},
    },
}

logging.config.dictConfig(LOGGING_CONFIG)
