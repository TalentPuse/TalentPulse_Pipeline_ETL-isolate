import logging
import boto3
from botocore.exceptions import ClientError
from src.utils.config import config

logger = logging.getLogger(__name__)

class MinioClient:
    """
    Wrapper for dealing with S3 API (MinIO).
    Handles bucket creation and file upload/download.
    """
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            endpoint_url=config.S3_ENDPOINT_URL,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            region_name='us-east-1' # default for S3 APIs
        )
        self._ensure_bucket_exists(config.S3_BUCKET_NAME)

    def _ensure_bucket_exists(self, bucket_name: str):
        """Creates the bucket if it doesn't exist."""
        try:
            self.s3_client.head_bucket(Bucket=bucket_name)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            # 404 means the bucket isn't there
            if str(error_code) == '404':
                logger.info(f"Bucket '{bucket_name}' not found. Creating it...")
                try:
                    self.s3_client.create_bucket(Bucket=bucket_name)
                except ClientError as ce:
                    logger.error(f"Failed to create bucket '{bucket_name}': {ce}")
                    raise
            else:
                # Other errors like 403 Forbidden etc
                logger.error(f"Error verifying bucket '{bucket_name}': {e}")
                raise

    def upload_string(self, bucket_name: str, object_name: str, content: str, content_type: str = "text/html"):
        """
        Upload string content (like HTML or JSON) to MinIO.
        """
        try:
            self.s3_client.put_object(
                Bucket=bucket_name,
                Key=object_name,
                Body=content.encode('utf-8'),
                ContentType=content_type
            )
            logger.info(f"Uploaded raw data: s3://{bucket_name}/{object_name}")
        except ClientError as e:
            logger.error(f"Failed to upload '{object_name}' to bucket '{bucket_name}': {e}")
            raise

    def upload_bytes(self, bucket_name: str, object_name: str, data: bytes, content_type: str = "application/octet-stream"):
        """
        Upload raw bytes (e.g. gzip-compressed HTML) to MinIO.
        """
        try:
            self.s3_client.put_object(
                Bucket=bucket_name,
                Key=object_name,
                Body=data,
                ContentType=content_type,
            )
            logger.info(f"Uploaded {len(data)} bytes: s3://{bucket_name}/{object_name}")
        except ClientError as e:
            logger.error(f"Failed to upload '{object_name}' to bucket '{bucket_name}': {e}")
            raise
