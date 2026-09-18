from __future__ import annotations

import io

import pytest

from loglens.config import AppConfig
from loglens.storage.s3 import (
    MAX_UPLOAD_BYTES,
    S3ObjectNotFoundError,
    S3Service,
    S3ServiceError,
    S3ValidationError,
)
from loglens.storage.services import create_s3_service


class ClientError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.put_calls = []
        self.get_calls = []
        self.error = None

    def put_object(self, **kwargs):
        if self.error:
            raise self.error
        self.put_calls.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

    def get_object(self, **kwargs):
        if self.error:
            raise self.error
        self.get_calls.append(kwargs)
        body = self.objects.get((kwargs["Bucket"], kwargs["Key"]))
        if body is None:
            raise ClientError("NoSuchKey")
        return {"Body": io.BytesIO(body)}


@pytest.fixture
def client():
    return FakeS3Client()


@pytest.fixture
def service(client):
    return S3Service("private-loglens-bucket", "test-region", client=client)


def test_valid_log_upload(service, client):
    result = service.upload(b"ERROR failed\n", "app.log", "user-1", "analysis-1")

    assert result.bucket == "private-loglens-bucket"
    assert result.key == "raw/user-1/analysis-1/app.log"
    assert result.content_type == "text/plain"
    assert result.size == 13
    assert client.put_calls[0]["ServerSideEncryption"] == "AES256"
    assert "ACL" not in client.put_calls[0]


def test_valid_txt_upload(service):
    result = service.upload("INFO started", "notes.txt", "user", "analysis")

    assert result.key.endswith("/notes.txt")
    assert result.size == len("INFO started".encode())


@pytest.mark.parametrize("filename", ["app.csv", "app", "app.LOGS"])
def test_unsupported_extension(service, filename):
    with pytest.raises(S3ValidationError, match="only .log and .txt"):
        service.upload(b"ERROR failed", filename, "user", "analysis")


def test_empty_file(service):
    with pytest.raises(S3ValidationError, match="empty"):
        service.upload(b"", "empty.log", "user", "analysis")


def test_null_byte_filename_is_rejected(service):
    with pytest.raises(S3ValidationError, match="invalid character"):
        service.upload(b"ERROR", "bad\x00.log", "user", "analysis")


def test_absolute_filename_cannot_escape_namespace(service):
    result = service.upload(b"ERROR", "/var/log/app.log", "user", "analysis")

    assert result.key == "raw/user/analysis/app.log"


def test_file_larger_than_10_mb(service):
    with pytest.raises(S3ValidationError, match="10 MB"):
        service.upload(b"x" * (MAX_UPLOAD_BYTES + 1), "large.log", "user", "analysis")


def test_binary_content_is_rejected(service):
    with pytest.raises(S3ValidationError, match="text data"):
        service.upload(b"ERROR\x00failed", "binary.log", "user", "analysis")


def test_path_traversal_filename_is_sanitized(service):
    result = service.upload(b"ERROR failed", "../../secrets.log", "user", "analysis")

    assert result.key == "raw/user/analysis/secrets.log"
    assert ".." not in result.key


def test_object_key_sanitizes_all_components():
    key = S3Service.build_object_key("user/../private", "a b", "..\\nested\\events.txt")

    assert key == "raw/user-..-private/a-b/events.txt"
    assert ".." not in key.split("/")[-1]


def test_metadata_contains_safe_context(service, client):
    service.upload(b"ERROR failed", "../../events.log", "user/one", "analysis one", "req/7")

    metadata = client.put_calls[0]["Metadata"]
    assert metadata == {
        "user-id": "user-one",
        "analysis-id": "analysis-one",
        "request-id": "req-7",
        "original-filename": "events.log",
        "content-type": "text/plain",
    }


def test_successful_object_retrieval(service, client):
    uploaded = service.upload(b"ERROR failed", "app.log", "user", "analysis")

    assert service.download(uploaded.key) == b"ERROR failed"
    assert client.get_calls[0]["Bucket"] == "private-loglens-bucket"


def test_missing_object(service):
    with pytest.raises(S3ObjectNotFoundError) as error:
        service.download("raw/user/analysis/missing.log")

    assert isinstance(error.value.__cause__, ClientError)


def test_aws_client_failure_is_wrapped_with_cause(service, client):
    client.error = ClientError("AccessDenied")

    with pytest.raises(S3ServiceError) as error:
        service.upload(b"ERROR failed", "app.log", "user", "analysis")

    assert isinstance(error.value.__cause__, ClientError)


def test_s3_configuration_validation():
    with pytest.raises(ValueError, match="S3_BUCKET_NAME"):
        create_s3_service(AppConfig(storage_mode="local", aws_region="test-region"))


def test_aws_config_requires_s3_bucket(monkeypatch):
    monkeypatch.setenv("LOGLENS_STORAGE_MODE", "aws")
    monkeypatch.setenv("AWS_REGION", "test-region")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test-table")
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)

    with pytest.raises(ValueError, match="S3_BUCKET_NAME"):
        AppConfig.from_env()


def test_s3_factory_uses_configured_bucket_and_region(monkeypatch):
    config = AppConfig(
        storage_mode="local",
        aws_region="configured-region",
        s3_bucket_name="configured-bucket",
    )
    created = {}

    class FakeService:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr("loglens.storage.services.S3Service", FakeService)
    assert isinstance(create_s3_service(config), FakeService)
    assert created == {
        "bucket_name": "configured-bucket",
        "region_name": "configured-region",
        "endpoint_url": None,
    }