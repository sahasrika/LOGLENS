from __future__ import annotations

import logging
import mimetypes
import posixpath
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = frozenset({".log", ".txt"})
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


class S3ServiceError(RuntimeError):
    """Base exception for S3 service failures."""


class S3ValidationError(S3ServiceError, ValueError):
    """Raised when an upload does not meet LogLens input requirements."""


class S3ObjectNotFoundError(S3ServiceError):
    """Raised when the requested S3 object does not exist."""


@dataclass(frozen=True)
class S3ObjectReference:
    """Location and content information returned after an upload."""

    bucket: str
    key: str
    content_type: str
    size: int


class S3Service:
    """Store and retrieve validated log files in a configured private bucket."""

    def __init__(
        self,
        bucket_name: str,
        region_name: str,
        client: Any | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        if not bucket_name.strip():
            raise ValueError("bucket_name must not be empty")
        if not region_name.strip():
            raise ValueError("region_name must not be empty")
        self.bucket_name = bucket_name
        self.region_name = region_name
        self._client = client or self._create_client(region_name, endpoint_url)

    @staticmethod
    def _create_client(region_name: str, endpoint_url: str | None) -> Any:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError("boto3 is required to use S3Service") from exc
        return boto3.client("s3", region_name=region_name, endpoint_url=endpoint_url)

    def upload(
        self,
        content: bytes | str,
        filename: str,
        user_id: str,
        analysis_id: str,
        request_id: str | None = None,
    ) -> S3ObjectReference:
        """Validate and upload content, returning its private S3 location."""
        raw, _, content_type = self._validate_upload(content, filename)
        key = self.build_object_key(user_id, analysis_id, filename)
        metadata = self.build_metadata(
            user_id=user_id,
            analysis_id=analysis_id,
            request_id=request_id,
            original_filename=filename,
            content_type=content_type,
        )
        try:
            self._client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=raw,
                ContentType=content_type,
                Metadata=metadata,
                ServerSideEncryption="AES256",
            )
        except Exception as exc:
            logger.exception("S3 upload failed for key %s", key)
            raise S3ServiceError("S3 upload failed") from exc
        return S3ObjectReference(self.bucket_name, key, content_type, len(raw))

    def download(self, key: str) -> bytes:
        """Download an object from this service's configured bucket."""
        if not self._is_safe_key(key):
            raise S3ValidationError("object key is invalid")
        try:
            response = self._client.get_object(Bucket=self.bucket_name, Key=key)
            return response["Body"].read()
        except Exception as exc:
            if self._is_missing_error(exc):
                raise S3ObjectNotFoundError("S3 object was not found") from exc
            logger.exception("S3 download failed for key %s", key)
            raise S3ServiceError("S3 download failed") from exc

    @classmethod
    def build_object_key(cls, user_id: str, analysis_id: str, filename: str) -> str:
        """Build a traversal-safe, predictable raw-upload key."""
        safe_user = cls._sanitize_component(user_id, "user")
        safe_analysis = cls._sanitize_component(analysis_id, "analysis")
        safe_filename = cls._safe_filename(filename)
        return posixpath.join("raw", safe_user, safe_analysis, safe_filename)

    @classmethod
    def build_metadata(
        cls,
        user_id: str,
        analysis_id: str,
        request_id: str | None,
        original_filename: str,
        content_type: str,
    ) -> dict[str, str]:
        """Build safe S3 metadata without credentials or log contents."""
        return {
            "user-id": cls._sanitize_component(user_id, "user"),
            "analysis-id": cls._sanitize_component(analysis_id, "analysis"),
            "request-id": cls._sanitize_component(request_id or "unknown", "request"),
            "original-filename": cls._safe_filename(original_filename),
            "content-type": content_type,
        }

    @classmethod
    def _validate_upload(
        cls, content: bytes | str, filename: str
    ) -> tuple[bytes, str, str]:
        if not filename or not filename.strip():
            raise S3ValidationError("filename must not be empty")
        if "\x00" in filename:
            raise S3ValidationError("filename contains an invalid character")
        safe_filename = cls._safe_filename(filename)
        extension = posixpath.splitext(safe_filename)[1].lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise S3ValidationError("only .log and .txt files are supported")
        raw = content.encode("utf-8") if isinstance(content, str) else content
        if not isinstance(raw, bytes):
            raise S3ValidationError("content must be bytes or text")
        if not raw:
            raise S3ValidationError("upload must not be empty")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise S3ValidationError("upload exceeds the 10 MB maximum")
        if b"\x00" in raw:
            raise S3ValidationError("upload must contain text data")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise S3ValidationError("upload must be valid UTF-8 text") from exc
        content_type = mimetypes.types_map.get(extension, "text/plain")
        return raw, safe_filename, content_type

    @classmethod
    def _safe_filename(cls, filename: str) -> str:
        basename = posixpath.basename(filename.replace("\\", "/"))
        safe = cls._sanitize_component(basename, "upload.log")
        if safe in {".", "..", ""}:
            raise S3ValidationError("filename is invalid")
        return safe[:255]

    @staticmethod
    def _sanitize_component(value: str, fallback: str) -> str:
        sanitized = _SAFE_COMPONENT_RE.sub("-", str(value).strip()).strip(".-")
        return sanitized[:128] or fallback

    @staticmethod
    def _is_safe_key(key: str) -> bool:
        normalized = posixpath.normpath(key)
        return bool(key) and normalized == key and not key.startswith(("/", "\\"))

    @staticmethod
    def _is_missing_error(exc: Exception) -> bool:
        response = getattr(exc, "response", {})
        code = response.get("Error", {}).get("Code")
        return code in {"404", "NoSuchKey", "NotFound"}