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
    with pytest.raises(MalformedDiagnosisError):
        parse_diagnosis_output({**valid, "unsupported": True})
    with pytest.raises(MalformedDiagnosisError):
        parse_diagnosis_output({**valid, "confidence": 2})
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
    assert response.json()["confidence"] == 0.2