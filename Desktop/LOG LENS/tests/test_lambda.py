import json
from types import SimpleNamespace

import pytest

from api.application import ApplicationValidationError, build_application
from api.lambda_handler import get_application, lambda_handler
from loglens.config import AppConfig


class FakeApplication:
    def __init__(self, result=None, error=None):
        self.result = result or {"total_errors": 0, "errors": []}
        self.error = error
        self.calls = []

    def __call__(self, text, request_id=None):
        self.calls.append(text)
        if self.error:
            raise self.error
        return self.result


def event(body, request_id="request-123"):
    return {"body": json.dumps(body), "requestContext": {"requestId": request_id}}


def test_successful_invocation_serializes_response():
    application = FakeApplication({"total_errors": 1, "errors": [{"fingerprint": "abc"}]})

    response = lambda_handler(event({"text": "ERROR failed"}), None, application)

    assert response["statusCode"] == 200
    assert response["headers"]["X-Request-ID"] == "request-123"
    assert json.loads(response["body"]) == {"total_errors": 1, "errors": [{"fingerprint": "abc"}]}
    assert application.calls == ["ERROR failed"]


def test_invalid_json_returns_bad_request():
    response = lambda_handler({"body": "not-json"}, None, FakeApplication())

    assert response["statusCode"] == 400
    assert "Invalid request" in response["body"]


def test_application_validation_error_returns_422():
    response = lambda_handler(event({"text": ""}), None,
                              FakeApplication(error=ApplicationValidationError("bad text")))

    assert response["statusCode"] == 422
    assert json.loads(response["body"])["detail"] == "bad text"


def test_unexpected_error_does_not_leak_details():
    response = lambda_handler(event({"text": "ERROR failed"}), None,
                              FakeApplication(error=RuntimeError("secret database path")))

    assert response["statusCode"] == 500
    assert "secret database path" not in response["body"]
    assert "unexpected internal error" in response["body"]


def test_context_request_id_is_used_as_fallback():
    response = lambda_handler({"body": {"text": "ERROR failed"}},
                              SimpleNamespace(aws_request_id="aws-request-1"), FakeApplication())

    assert response["headers"]["X-Request-ID"] == "aws-request-1"


def test_missing_text_is_validation_error():
    response = lambda_handler(event({"log": "ERROR failed"}), None, FakeApplication())

    assert response["statusCode"] == 422


def test_cached_application_is_reused(monkeypatch):
    get_application.cache_clear()


def test_cached_application_initializes_storage_once(monkeypatch):
    created = []

    class FakeStorage:
        pass

    monkeypatch.setattr("api.lambda_handler.create_storage", lambda: created.append(FakeStorage()) or created[-1])
    get_application.cache_clear()
    get_application()
    get_application()

    assert len(created) == 1
    get_application.cache_clear()


def test_build_application_forwards_request_id():
    calls = []
    storage = object()

    class FakePipeline:
        def __init__(self, storage):
            calls.append(storage)

        def process(self, text):
            calls.append(text)
            return []

    application = build_application(storage=storage, pipeline_factory=FakePipeline)
    result = application("ERROR failed", request_id="request-1")

    assert result == {"total_errors": 0, "errors": []}
    assert calls == [storage, "ERROR failed"]
    first = get_application()
    second = get_application()

    assert first is second
    get_application.cache_clear()


def test_application_rejects_oversized_text():
    from api.application import analyze_text

    with pytest.raises(ApplicationValidationError):
        analyze_text("x" * (10 * 1024 * 1024 + 1))


def test_missing_aws_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv("LOGLENS_STORAGE_MODE", "aws")
    monkeypatch.delenv("AWS_REGION", raising=False)

    with pytest.raises(ValueError, match="AWS_REGION"):
        AppConfig.from_env()


def test_configuration_contains_no_credentials():
    config = AppConfig(storage_mode="aws", aws_region="region", s3_bucket_name="bucket")

    assert not hasattr(config, "aws_access_key_id")
    assert not hasattr(config, "aws_secret_access_key")


def test_lambda_rejects_missing_auth_when_enabled(monkeypatch):
    config = AppConfig(
        auth_required=True,
        cognito_issuer="https://issuer.test",
        cognito_client_id="client",
        cognito_jwks_url="https://keys.test/jwks",
    )
    monkeypatch.setattr("api.lambda_handler.get_auth_config", lambda: config)

    response = lambda_handler(
        {"httpMethod": "POST", "path": "/analyze", "body": '{"text":"ERROR failed"}'},
        None,
        FakeApplication(),
    )

    assert response["statusCode"] == 401