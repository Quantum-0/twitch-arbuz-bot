import logging

from utils.singleton import singleton


logger = logging.getLogger(__name__)


@singleton
class ImageResizer():
    @staticmethod
    async def resize(image_b64: str, resolution: tuple[int, int] = (100, 100)) -> str:
        logger.info("Resizer is not implemented")
        return image_b64
