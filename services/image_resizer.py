import logging

from utils.singleton import singleton


logger = logging.getLogger(__name__)


@singleton
class ImageResizer:
    @staticmethod
    async def resize(input_image: bytes, resolution: tuple[int, int] = (100, 100)) -> bytes:
        logger.info("Resizer is not implemented")
        return input_image
