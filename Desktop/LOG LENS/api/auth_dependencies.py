"""Authentication dependencies for FastAPI and Lambda composition."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Header

from api.auth import AuthenticatedUser, AuthenticationError, CognitoJWTValidator, JWKSProvider, authorization_token
from loglens.config import AppConfig


@lru_cache(maxsize=1)
def get_auth_config() -> AppConfig:
    return AppConfig.from_env()


@lru_cache(maxsize=1)
def get_jwt_validator() -> CognitoJWTValidator:
    config = get_auth_config()
    if not config.auth_required:
        raise RuntimeError("JWT validator requested while authentication is disabled")
    return CognitoJWTValidator(
        config.cognito_issuer or "",
        config.cognito_client_id or "",
        JWKSProvider(config.cognito_jwks_url or ""),
        config.cognito_token_use,
    )


def require_user(authorization: str | None = Header(default=None)) -> AuthenticatedUser | None:
    config = get_auth_config()
    if not config.auth_required:
        return None
    return get_jwt_validator().validate(authorization_token(authorization))