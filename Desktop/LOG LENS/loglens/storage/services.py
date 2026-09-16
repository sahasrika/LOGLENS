"""Factories for infrastructure services."""

from __future__ import annotations

from loglens.config import AppConfig
from loglens.storage.s3 import S3Service


def create_s3_service(config: AppConfig | None = None) -> S3Service:
    """Create an S3 service from environment-backed configuration."""
    config = config or AppConfig.from_env()
    if not config.s3_bucket_name:
        raise ValueError("S3_BUCKET_NAME is required to use S3 storage")
    if not config.aws_region:
        raise ValueError("AWS_REGION is required to use S3 storage")
    return S3Service(
        bucket_name=config.s3_bucket_name,
        region_name=config.aws_region,
        endpoint_url=config.s3_endpoint_url,
    )