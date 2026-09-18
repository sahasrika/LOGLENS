"""FastAPI dependency wiring for the shared application service."""

from functools import lru_cache

from api.service import ApplicationService
from api.diagnosis import BedrockDiagnosisService, MockDiagnosisService
from loglens.config import AppConfig
from loglens.storage.factory import create_storage
from loglens.storage.services import create_s3_service


@lru_cache(maxsize=1)
def get_application_service() -> ApplicationService:
    """Create one local-process service and reuse its storage between requests."""
    config = AppConfig.from_env()
    s3_service = create_s3_service(config) if config.storage_mode == "aws" else None
    diagnosis_service = MockDiagnosisService()
    if config.bedrock_model_id:
        try:
            import boto3
            bedrock_client = boto3.client("bedrock-runtime", region_name=config.aws_region)
            diagnosis_service = BedrockDiagnosisService(bedrock_client, config.bedrock_model_id)
        except Exception:
            diagnosis_service = BedrockDiagnosisService(model_id=config.bedrock_model_id)
    return ApplicationService(
        storage=create_storage(config),
        diagnosis_service=diagnosis_service,
        s3_service=s3_service,
    )