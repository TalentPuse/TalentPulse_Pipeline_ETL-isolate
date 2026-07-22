import pytest
from botocore.exceptions import ClientError
from unittest.mock import patch, MagicMock
from src.storage.minio_client import MinioClient
from src.utils.config import config


@pytest.fixture(autouse=True)
def fake_s3_credentials():
    """Give every test in this module throwaway S3 credentials.

    MinioClient refuses to construct without them (a deliberate fail-fast, see
    minio_client.py), and these tests exercise bucket/upload behaviour rather
    than auth.

    Patching the config object matters more than it looks: relying on the
    ambient environment made these tests pass on a developer machine — whose
    .env supplies real keys — and fail in CI, where no .env exists. The suite
    must not care whether a .env happens to be sitting next to it.
    """
    with patch.object(config, "S3_ACCESS_KEY", "test-access-key"), \
         patch.object(config, "S3_SECRET_KEY", "test-secret-key"):
        yield


@patch("src.storage.minio_client.boto3.client")
def test_minio_client_initialization_bucket_exists(mock_boto_client):
    """Test client initializes cleanly when the bucket already exists."""
    mock_s3 = MagicMock()
    mock_boto_client.return_value = mock_s3

    # head_bucket succeeds naturally
    client = MinioClient()

    mock_s3.head_bucket.assert_called_once()
    mock_s3.create_bucket.assert_not_called()


@patch("src.storage.minio_client.boto3.client")
def test_minio_client_initialization_creates_bucket(mock_boto_client):
    """Test client creates the bucket if it is missing (404 error)."""
    mock_s3 = MagicMock()
    mock_boto_client.return_value = mock_s3

    # Simulate 404 (Not Found) on head_bucket
    error_response = {'Error': {'Code': '404', 'Message': 'Not Found'}}
    mock_s3.head_bucket.side_effect = ClientError(error_response, 'HeadBucket')

    client = MinioClient()

    mock_s3.head_bucket.assert_called_once()
    mock_s3.create_bucket.assert_called_once()


@patch("src.storage.minio_client.boto3.client")
def test_upload_string(mock_boto_client):
    """Test successful upload."""
    mock_s3 = MagicMock()
    mock_boto_client.return_value = mock_s3

    client = MinioClient()

    client.upload_string("test-bucket", "folder/file.html", "<html>hello</html>")

    mock_s3.put_object.assert_called_once()
    _, kwargs = mock_s3.put_object.call_args
    assert kwargs["Bucket"] == "test-bucket"
    assert kwargs["Key"] == "folder/file.html"
    assert kwargs["Body"] == b"<html>hello</html>"


@pytest.mark.parametrize(
    "access_key, secret_key",
    [("", "some-secret"), ("some-access", ""), ("", "")],
)
@patch("src.storage.minio_client.boto3.client")
def test_missing_credentials_raise_before_client_is_built(
    mock_boto_client, access_key, secret_key
):
    """Absent credentials must fail at construction, not at the first upload.

    These keys used to default to minioadmin/minioadmin — MinIO's well-known
    admin pair — so a deploy that forgot to set them built a client happily and
    only broke much later, with an opaque S3 error that named nothing useful.
    Asserting boto3 is never reached pins the failure to the earliest point.
    """
    with patch.object(config, "S3_ACCESS_KEY", access_key), \
         patch.object(config, "S3_SECRET_KEY", secret_key):
        with pytest.raises(RuntimeError, match="S3_ACCESS_KEY / S3_SECRET_KEY"):
            MinioClient()

    mock_boto_client.assert_not_called()
