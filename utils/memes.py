import logging.config
from datetime import UTC, datetime

from memealerts import MemealertsAsyncClient
from memealerts.types.exceptions import MATokenExpiredError

from utils.logging_conf import LOGGING_CONFIG

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


async def token_expires_in_days(memealerts_token) -> int:
    try:
        async with MemealertsAsyncClient(memealerts_token) as cli:
            expires_in: datetime = cli.token_expires_in
            return int((expires_in - datetime.now(UTC)).days)
    except MATokenExpiredError:
        return 0
