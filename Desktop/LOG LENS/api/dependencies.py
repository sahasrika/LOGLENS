"""FastAPI dependency wiring for the shared application service."""

from functools import lru_cache

from api.service import ApplicationService
from loglens.config import AppConfig
from loglens.storage.factory import create_storage
from loglens.storage.services import create_s3_service


@lru_cache(maxsize=1)
def get_application_service() -> ApplicationService:
    """Create one local-process service and reuse its storage between requests."""
    config = AppConfig.from_env()
    s3_service = create_s3_service(config) if config.storage_mode == "aws" else None
    return ApplicationService(storage=create_storage(config), s3_service=s3_service)