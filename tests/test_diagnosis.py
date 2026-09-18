from __future__ import annotations

import json
from datetime import datetime

import pytest

from api.diagnosis import (
    BedrockDiagnosisService,
    Diagnosis,
    DiagnosisEvidence,
    DiagnosisProviderUnavailableError,
    DiagnosisServiceError,
    InvalidEvidenceError,
    MalformedDiagnosisError,
    MockDiagnosisService,
    parse_diagnosis_output,
    redact_sensitive_data,
    serialize_evidence,
)
from api.service import ApplicationService, OwnershipNotConfiguredError
from api.dependencies import get_application_service
from api.main import app
from fastapi.testclient import TestClient
from loglens.models import ErrorRecord
from loglens.storage.base import InMemoryStorage


def record(fingerprint="fp-1"):
    return ErrorRecord(
        fingerprint=fingerprint, error_type="ValueError", message="parse failed",
        occurrences=2, first_seen=datetime(2026, 1, 1), last_seen=datetime(2026, 1, 2),
        severity="ERROR", source_service="parser", sample_logs=["ERROR parse failed"],
    )


def test_mock_diagnosis_is_valid_and_deterministic():
    service = MockDiagnosisService()
    first = service.diagnose([record()])
    second = service.diagnose([record()])

    assert first == second
    assert first.confidence == 0.2
    assert first.evidence[0].fingerprint == "fp-1"


def test_mock_supports_multiple_records_in_stable_order():
    diagnosis = MockDiagnosisService().diagnose([record("fp-2"), record("fp-1")])

    assert [item.fingerprint for item in diagnosis.evidence] == ["fp-1", "fp-2"]


@pytest.mark.parametrize("evidence", [[], ["not-an-error-record"]])
def test_empty_or_invalid_evidence_is_rejected(evidence):
    with pytest.raises(InvalidEvidenceError):
        serialize_evidence(evidence)


def test_evidence_serialization_is_deterministic_and_bounded_to_record_fields():
    serialized = serialize_evidence([record()])
    payload = json.loads(serialized)

    assert list(payload) == ["evidence"]
    assert list(payload["evidence"][0]) == sorted(payload["evidence"][0])
    assert "password" not in serialized.lower()


def test_strict_diagnosis_output_validation():
    valid = {
        "summary": "Observed failure",
        "root_cause": "Insufficient evidence",
        "confidence": 0.25,
        "evidence": [{"fingerprint": "fp-1", "observation": "Observed error"}],
        "recommendations": ["Review logs"],
    }
    assert isinstance(parse_diagnosis_output(valid), Diagnosis)
    ignored = parse_diagnosis_output({**valid, "unsupported": True})
    assert ignored.confidence == 0.25
    percent = parse_diagnosis_output({**valid, "confidence": 80})
    assert percent.confidence == 0.8
    missing = parse_diagnosis_output({k: v for k, v in valid.items() if k != "confidence"})
    assert missing.confidence is None
    invalid = parse_diagnosis_output({**valid, "confidence": 250})
    assert invalid.confidence is None
    with pytest.raises(MalformedDiagnosisError):
        parse_diagnosis_output(json.dumps({"summary": "missing fields"}))


def test_bedrock_boundary_is_explicitly_unavailable():
    with pytest.raises(DiagnosisProviderUnavailableError):
        BedrockDiagnosisService().diagnose([record()])


def test_bedrock_service_with_mock_client():
    class MockBedrockBody:
        def read(self):
            return json.dumps({
                "content": [{
                    "text": json.dumps({
                        "summary": "Database connection timeout occurred",
                        "root_cause": "Database server took too long to respond",
                        "confidence": 0.9,
                        "evidence": [{"fingerprint": "fp-1", "observation": "Connection timed out"}],
                        "recommendations": ["Increase timeout and add retry logic"]
                    })
                }]
            }).encode("utf-8")

    class MockBedrockClient:
        def invoke_model(self, **kwargs):
            return {"body": MockBedrockBody()}

    service = BedrockDiagnosisService(client=MockBedrockClient(), model_id="test-model")
    diagnosis = service.diagnose([record()])

    assert diagnosis.summary == "Database connection timeout occurred"
    assert diagnosis.confidence == 0.9
    assert diagnosis.evidence[0].fingerprint == "fp-1"


def test_bedrock_service_uses_converse_with_supplied_evidence():
    calls = []

    class ConverseClient:
        def converse(self, **kwargs):
            calls.append(kwargs)
            return {
                "output": {
                    "message": {
                        "content": [{
                            "text": json.dumps({
                                "summary": "Observed failure",
                                "root_cause": "The supplied operation failed",
                                "confidence": 0.8,
                                "evidence": [{
                                    "fingerprint": "fp-1",
                                    "observation": "The supplied error occurred",
                                }],
                                "recommendations": ["Review the failing operation"],
                            })
                        }]
                    }
                }
            }

    diagnosis = BedrockDiagnosisService(
        client=ConverseClient(), model_id="amazon.nova-lite-v1:0"
    ).diagnose([record()])

    assert diagnosis.root_cause == "The supplied operation failed"
    assert calls[0]["modelId"] == "amazon.nova-lite-v1:0"
    assert calls[0]["messages"][0]["content"][0]["text"].count("fp-1") == 1
    assert "parse failed" in calls[0]["messages"][0]["content"][0]["text"]


def test_application_diagnosis_uses_service_in_local_mode():
    storage = InMemoryStorage()
    storage.upsert(record())
    result = ApplicationService(storage).diagnose("fp-1")

    assert result["evidence"][0]["fingerprint"] == "fp-1"


def test_application_diagnosis_requires_ownership_for_authenticated_user():
    storage = InMemoryStorage()
    storage.upsert(record())
    user = object()

    with pytest.raises(OwnershipNotConfiguredError):
        ApplicationService(storage).diagnose("fp-1", user=user)


def test_application_wraps_provider_failure():
    class FailingService:
        def diagnose(self, records):
            raise RuntimeError("provider secret")

    storage = InMemoryStorage()
    storage.upsert(record())
    service = ApplicationService(storage, diagnosis_service=FailingService())

    with pytest.raises(DiagnosisServiceError) as error:
        service.diagnose("fp-1")
    assert "provider secret" not in str(error.value)


def test_diagnosis_endpoint_returns_structured_mock_result():
    service = ApplicationService(InMemoryStorage())
    result = service.analyze("ERROR:svc:Failure user=1")
    app.dependency_overrides[get_application_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(f"/errors/{result['errors'][0]['fingerprint']}/diagnose")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert "fingerprint" in data
    assert data["diagnosis"]["confidence"] == 0.2


def test_bedrock_service_handles_missing_model_id():
    service = BedrockDiagnosisService(client=object(), model_id="")
    with pytest.raises(DiagnosisProviderUnavailableError) as exc:
        service.diagnose([record()])
    assert "BEDROCK_MODEL_ID" in str(exc.value)


def test_redact_sensitive_data_handles_tokens_and_jwt():
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature AND password=secret123 AKIAIOSFODNN7EXAMPLE"
    redacted = redact_sensitive_data(text)
    assert "[REDACTED_TOKEN]" in redacted
    assert "[REDACTED]" in redacted
    assert "[REDACTED_AWS_KEY]" in redacted
    assert "secret123" not in redacted
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted


def test_bedrock_service_handles_exceptions():
    class ExceptionClient:
        def invoke_model(self, **kwargs):
            raise RuntimeError("AccessDeniedException: User is not authorized")

    service = BedrockDiagnosisService(client=ExceptionClient(), model_id="global.anthropic.claude-sonnet-4-6")
    with pytest.raises(DiagnosisServiceError) as exc:
        service.diagnose([record()])
    assert "Bedrock invocation failed" in str(exc.value)