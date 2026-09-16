from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from api.auth import (
    AuthenticatedUser,
    AuthenticationError,
    CognitoJWTValidator,
    JWKSProvider,
    authorization_token,
)
from api.auth_dependencies import get_auth_config, get_jwt_validator
from api.main import app
from loglens.config import AppConfig
from loglens.storage.base import InMemoryStorage
from api.service import ApplicationService


ISSUER = "https://cognito-idp.test/region_pool"
CLIENT_ID = "client-123"


@pytest.fixture
def key_material():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key()
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(public)
    import json
    document = {"keys": [{**json.loads(jwk), "kid": "key-1", "alg": "RS256", "use": "sig"}]}
    return private, document


def token(private, **claims):
    payload = {"sub": "user-1", "client_id": CLIENT_ID, "token_use": "access",
               "iss": ISSUER, "exp": int(time.time()) + 300, **claims}
    return jwt.encode(payload, private, algorithm="RS256", headers={"kid": "key-1"})


def validator(private, document, fetcher=None):
    provider = JWKSProvider("https://keys.test/jwks", fetcher=fetcher or (lambda _: document))
    return CognitoJWTValidator(ISSUER, CLIENT_ID, provider)


@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer "])
def test_missing_or_malformed_authorization(header):
    with pytest.raises(AuthenticationError):
        authorization_token(header)


def test_invalid_jwt(key_material):
    private, document = key_material
    with pytest.raises(AuthenticationError):
        validator(private, document).validate("not-a-jwt")


@pytest.mark.parametrize("claims", [
    {"exp": int(time.time()) - 1},
    {"iss": "https://wrong"},
    {"client_id": "wrong-client"},
    {"token_use": "id"},
])
def test_invalid_claims(key_material, claims):
    private, document = key_material
    with pytest.raises(AuthenticationError):
        validator(private, document).validate(token(private, **claims))


def test_missing_subject_is_rejected(key_material):
    private, document = key_material
    with pytest.raises(AuthenticationError):
        validator(private, document).validate(token(private, sub=None))


def test_blank_subject_is_rejected(key_material):
    private, document = key_material
    with pytest.raises(AuthenticationError):
        validator(private, document).validate(token(private, sub=" "))


def test_invalid_signature(key_material):
    private, document = key_material
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthenticationError):
        validator(private, document).validate(token(other))


def test_valid_jwt_extracts_minimal_user_context(key_material):
    private, document = key_material
    user = validator(private, document).validate(token(private, username="alice"))

    assert user == AuthenticatedUser("user-1", "alice", "access", CLIENT_ID)


def test_unsigned_token_is_rejected(key_material):
    _, document = key_material
    unsigned = jwt.encode({"sub": "user-1"}, key=None, algorithm="none")
    with pytest.raises(AuthenticationError):
        validator(None, document).validate(unsigned)


def test_jwks_retrieval_and_cache(key_material):
    private, document = key_material
    calls = []
    provider = JWKSProvider("https://keys.test/jwks", fetcher=lambda url: calls.append(url) or document)
    check = CognitoJWTValidator(ISSUER, CLIENT_ID, provider)

    check.validate(token(private))
    check.validate(token(private))

    assert calls == ["https://keys.test/jwks"]


def test_jwks_cache_expiry_refetches(key_material):
    private, document = key_material
    calls = []
    provider = JWKSProvider("https://keys.test/jwks", fetcher=lambda url: calls.append(url) or document,
                            cache_ttl_seconds=0)
    check = CognitoJWTValidator(ISSUER, CLIENT_ID, provider)
    check.validate(token(private))
    check.validate(token(private))
    assert len(calls) == 2


def test_fastapi_health_public_but_protected_analyze_requires_auth(monkeypatch):
    config = AppConfig(auth_required=True, cognito_issuer=ISSUER,
                       cognito_client_id=CLIENT_ID, cognito_jwks_url="https://keys.test/jwks")
    monkeypatch.setattr("api.auth_dependencies.get_auth_config", lambda: config)
    with TestClient(app, raise_server_exceptions=False) as client:
        health = client.get("/health")
        protected = client.post("/analyze", json={"text": "ERROR failure"})
    assert health.status_code == 200
    assert protected.status_code == 401


def test_fastapi_valid_authenticated_request_reaches_application(monkeypatch, key_material):
    private, document = key_material
    config = AppConfig(auth_required=True, cognito_issuer=ISSUER,
                       cognito_client_id=CLIENT_ID, cognito_jwks_url="https://keys.test/jwks")
    monkeypatch.setattr("api.auth_dependencies.get_auth_config", lambda: config)
    monkeypatch.setattr("api.auth_dependencies.get_jwt_validator", lambda: validator(private, document))
    service = ApplicationService(InMemoryStorage())
    from api.dependencies import get_application_service
    app.dependency_overrides[get_application_service] = lambda: service
    with TestClient(app) as client:
        response = client.post("/analyze", json={"text": "ERROR failure"},
                               headers={"Authorization": f"Bearer {token(private)}"})
    app.dependency_overrides.clear()
    assert response.status_code == 200


def test_auth_failure_does_not_leak_crypto_details(key_material):
    private, document = key_material
    with pytest.raises(AuthenticationError) as error:
        validator(private, document).validate("Bearer secret-token")
    assert "secret-token" not in str(error.value)


def test_aws_mode_cannot_disable_authentication(monkeypatch):
    monkeypatch.setenv("LOGLENS_STORAGE_MODE", "aws")
    monkeypatch.setenv("AWS_REGION", "test-region")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test-table")
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("LOGLENS_AUTH_REQUIRED", "false")

    with pytest.raises(ValueError, match="LOGLENS_AUTH_REQUIRED"):
        AppConfig.from_env()