"""Environment-based configuration for LogLens application wiring."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    """Runtime settings used to choose local or AWS storage."""

    storage_mode: str = "local"
    aws_region: str | None = None
    dynamodb_table_name: str | None = None
    dynamodb_endpoint_url: str | None = None
    s3_bucket_name: str | None = None
    s3_endpoint_url: str | None = None
    auth_required: bool = False
    cognito_issuer: str | None = None
    cognito_client_id: str | None = None
    cognito_jwks_url: str | None = None
    cognito_token_use: str = "access"
    bedrock_model_id: str = "global.anthropic.claude-sonnet-4-6"

    @classmethod
    def from_env(cls) -> "AppConfig":
        mode = os.getenv("LOGLENS_STORAGE_MODE", "local").strip().lower()
        if mode not in {"local", "aws"}:
            raise ValueError("LOGLENS_STORAGE_MODE must be 'local' or 'aws'")

        config = cls(
            storage_mode=mode,
            aws_region=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
            dynamodb_table_name=os.getenv("DYNAMODB_TABLE_NAME"),
            dynamodb_endpoint_url=os.getenv("DYNAMODB_ENDPOINT_URL"),
            s3_bucket_name=os.getenv("S3_BUCKET_NAME"),
            s3_endpoint_url=os.getenv("S3_ENDPOINT_URL"),
            auth_required=os.getenv("LOGLENS_AUTH_REQUIRED", "false").strip().lower()
            in {"1", "true", "yes"},
            cognito_issuer=os.getenv("COGNITO_ISSUER"),
            cognito_client_id=os.getenv("COGNITO_CLIENT_ID"),
            cognito_jwks_url=os.getenv("COGNITO_JWKS_URL"),
            cognito_token_use=os.getenv("COGNITO_TOKEN_USE", "access").strip().lower(),
            bedrock_model_id=os.getenv(
                "BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6"
            ).strip(),
        )
        if config.storage_mode == "aws":
            if not config.aws_region:
                raise ValueError("AWS_REGION is required when AWS storage is enabled")
            if not config.dynamodb_table_name:
                raise ValueError(
                    "DYNAMODB_TABLE_NAME is required when AWS storage is enabled"
                )
            if not config.s3_bucket_name:
                raise ValueError(
                    "S3_BUCKET_NAME is required when AWS storage is enabled"
                )
            if not config.auth_required:
                raise ValueError(
                    "LOGLENS_AUTH_REQUIRED must be true when AWS storage is enabled"
                )
        if config.auth_required:
            missing = [
                name for name, value in (
                    ("COGNITO_ISSUER", config.cognito_issuer),
                    ("COGNITO_CLIENT_ID", config.cognito_client_id),
                    ("COGNITO_JWKS_URL", config.cognito_jwks_url),
                ) if not value
            ]
            if missing:
                raise ValueError(f"Missing authentication configuration: {', '.join(missing)}")
            if config.cognito_token_use not in {"access", "id"}:
                raise ValueError("COGNITO_TOKEN_USE must be 'access' or 'id'")
        return config