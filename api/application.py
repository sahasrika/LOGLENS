"""Framework-neutral application service used by FastAPI and Lambda."""

from __future__ import annotations

from typing import Callable

from loglens.pipeline import LogLensPipeline
from loglens.storage.base import BaseStorage
from loglens.storage.factory import create_storage

MAX_TEXT_BYTES = 10 * 1024 * 1024


class ApplicationValidationError(ValueError):
    """Raised for invalid application input."""


def analyze_text(text: str, storage: BaseStorage | None = None,
                 pipeline_factory: Callable[..., LogLensPipeline] = LogLensPipeline,
                 request_id: str | None = None) -> dict:
    """Analyze text and return a JSON-compatible response payload."""
    if not isinstance(text, str) or not text.strip():
        raise ApplicationValidationError("text must not be empty or whitespace-only")
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ApplicationValidationError("text exceeds maximum size of 10 MB")
    pipeline = pipeline_factory(storage=storage if storage is not None else create_storage())
    records = pipeline.process(text)
    return {"total_errors": len(records), "errors": [record.to_dict() for record in records]}


def build_application(
    storage: BaseStorage | None = None,
    pipeline_factory: Callable[..., LogLensPipeline] = LogLensPipeline,
) -> Callable[..., dict]:
    """Build an application callable with dependencies captured for reuse."""
    configured_storage = storage or create_storage()

    def application(text: str, request_id: str | None = None) -> dict:
        return analyze_text(
            text,
            storage=configured_storage,
            pipeline_factory=pipeline_factory,
            request_id=request_id,
        )

    return application