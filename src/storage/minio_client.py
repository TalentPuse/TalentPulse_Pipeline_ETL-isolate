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
        # Fail here, not on the first upload. These credentials used to default to
        # minioadmin/minioadmin — MinIO's well-known admin pair — so a deploy that
        # forgot to set them still built a client happily and only broke later,
        # with an opaque S3 error that said nothing about the real cause.
        if not config.S3_ACCESS_KEY or not config.S3_SECRET_KEY:
            raise RuntimeError(
                "S3_ACCESS_KEY / S3_SECRET_KEY are not set. Refusing to build an "
                "object-storage client without credentials."
            )
        self.s3_client = boto3.client(
            's3',
            endpoint_url=config.S3_ENDPOINT_URL,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            region_name=config.S3_REGION,
        )
        self._ensure_bucket_exists(config.S3_BUCKET_NAME)

    def _ensure_bucket_exists(self, bucket_name: str):
        """Creates the bucket if it doesn't exist.

        Tolerant of providers (e.g. Cloudflare R2) that disallow bucket
        creation via the S3 API — in that setup the bucket is pre-created
        out-of-band (see docs/gha-migration-runbook.md), so a failed
        create_bucket call is logged as a warning instead of raised, as
        long as head_bucket confirms it already exists.
        """
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
                    # Re-check: some providers (e.g. R2) reject API bucket
                    # creation outright even though the bucket already
                    # exists (pre-created by the operator). Only crash if
                    # it genuinely isn't there.
                    try:
                        self.s3_client.head_bucket(Bucket=bucket_name)
                        logger.warning(
                            f"create_bucket for '{bucket_name}' failed ({ce}), but the bucket "
                            "already exists (confirmed via head_bucket) — continuing."
                        )
                    except ClientError:
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
