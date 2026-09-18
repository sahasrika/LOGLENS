from __future__ import annotations

from fastapi.testclient import TestClient

from api.dependencies import get_application_service
from api.main import app
from api.service import ApplicationService
from loglens.storage.base import InMemoryStorage


def make_client() -> TestClient:
    service = ApplicationService(InMemoryStorage())
    app.dependency_overrides[get_application_service] = lambda: service
    return TestClient(app, raise_server_exceptions=False)


def teardown_function():
    app.dependency_overrides.clear()


def test_health_is_public_and_returns_request_id():
    with make_client() as client:
        response = client.get("/health", headers={"X-Request-ID": "client-request"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "loglens"}
    assert response.headers["X-Request-ID"] == "client-request"


def test_analyze_returns_analysis_reference_and_can_be_retrieved():
    with make_client() as client:
        response = client.post(
            "/analyze",
            json={"text": "2026-08-16 12:00:00 ERROR [svc] Failed for user 123"},
        )
        analysis = response.json()
        fetched = client.get(f"/analyses/{analysis['analysis_id']}")

    assert response.status_code == 200
    assert analysis["status"] == "completed"
    assert analysis["total_errors"] == 1
    assert fetched.status_code == 200
    assert fetched.json()["analysis_id"] == analysis["analysis_id"]


def test_analyze_validation_and_size_errors():
    with make_client() as client:
        empty = client.post("/analyze", json={"text": " "})
        oversized = client.post("/analyze", json={"text": "x" * (10 * 1024 * 1024 + 1)})

    assert empty.status_code == 422
    assert oversized.status_code == 422
    assert "detail" in empty.json()


def test_errors_list_and_single_error_lookup():
    with make_client() as client:
        analyzed = client.post("/analyze", json={"text": "ERROR:svc:Failure user=1"}).json()
        errors = client.get("/errors")
        fingerprint = errors.json()[0]["fingerprint"]
        single = client.get(f"/errors/{fingerprint}")
        missing = client.get("/errors/missing")

    assert analyzed["total_errors"] == 1
    assert errors.status_code == 200
    assert single.status_code == 200
    assert single.json()["fingerprint"] == fingerprint
    assert missing.status_code == 404
    assert "detail" in missing.json()


def test_missing_analysis_returns_404():
    with make_client() as client:
        response = client.get("/analyses/missing")

    assert response.status_code == 404
    assert "detail" in response.json()


def test_upload_contract_validates_and_does_not_fake_upload():
    with make_client() as client:
        valid = client.post("/uploads", json={"filename": "app.log", "size": 42})
        invalid = client.post("/uploads", json={"filename": "app.csv", "size": 42})

    assert valid.status_code == 501
    assert valid.json()["status"] == "not_configured"
    assert invalid.status_code == 422


def test_diagnose_contract_is_explicit_and_no_fake_result():
    with make_client() as client:
        response = client.post("/errors/missing/diagnose")

    assert response.status_code == 404
    assert "diagnosis" not in response.json()


def test_openapi_contains_phase_four_routes():
    with make_client() as client:
        paths = client.get("/openapi.json").json()["paths"]

    assert {"/health", "/analyze", "/uploads", "/errors", "/errors/{fingerprint}",
            "/errors/{fingerprint}/diagnose", "/analyses/{analysis_id}"}.issubset(paths)