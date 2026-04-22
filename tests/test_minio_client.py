import pytest
from botocore.exceptions import ClientError
from unittest.mock import patch, MagicMock
from src.storage.minio_client import MinioClient

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
