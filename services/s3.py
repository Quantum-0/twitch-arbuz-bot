import aioboto3

from config import settings


from botocore import exceptions as s3exc


class S3Exception(Exception):
    pass

class FileNotExistError(S3Exception):
    pass


class FileStorage:
    def __init__(self, session: aioboto3.Session):
        self._session = session
        self._endpoint_url = settings.s3_url
        self._access_key = settings.s3_access_key
        self._secret_key = settings.s3_secret_key
        self._bucket = settings.s3_bucket
        self._client_kwargs = {
            "service_name": "s3",
            "endpoint_url": str(self._endpoint_url),
            "aws_access_key_id": self._access_key.get_secret_value(),
            "aws_secret_access_key": self._secret_key.get_secret_value(),
            "region_name": "us-east-1",  # для MinIO
        }

    async def put_object(self, key: str, data: bytes):
        async with self._session.client(**self._client_kwargs) as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data)

    async def get_object(self, key: str) -> bytes:
        try:
            async with self._session.client(**self._client_kwargs) as s3:
                response = await s3.get_object(Bucket=self._bucket, Key=key)
                async with response["Body"] as stream:
                    return await stream.read()
        except s3exc.ClientError as exc:
            raise FileNotExistError from exc