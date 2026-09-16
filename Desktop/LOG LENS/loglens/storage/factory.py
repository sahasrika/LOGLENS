"""Storage dependency wiring for local and AWS runtime modes."""

from __future__ import annotations

from loglens.config import AppConfig
from loglens.storage.base import BaseStorage, InMemoryStorage
from loglens.storage.dynamodb import DynamoDBStorage


def create_storage(config: AppConfig | None = None) -> BaseStorage:
    """Create the configured storage implementation."""
    config = config or AppConfig.from_env()
    if config.storage_mode == "local":
        return InMemoryStorage()
    return DynamoDBStorage(
        table_name=config.dynamodb_table_name or "",
        region_name=config.aws_region or "",
        endpoint_url=config.dynamodb_endpoint_url,
    )