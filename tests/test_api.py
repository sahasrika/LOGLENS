"""
API tests for the LogLens FastAPI application.

Uses FastAPI's TestClient (backed by httpx) so no real server needs to be
running.  Every test creates its own client instance; no state is shared
between tests.

Coverage
--------
Health endpoint
  - GET /health returns 200 with correct body

POST /analyze (JSON text)
  - valid single error
  - valid multiple errors — different patterns become separate records
  - repeated same-pattern errors are aggregated (occurrences > 1)
  - INFO lines are excluded from results
  - comment lines (#...) are ignored
  - empty text returns 422
  - whitespace-only text returns 422
  - missing "text" field returns 422
  - text exceeding size limit returns 422

POST /analyze/upload (multipart file)
  - .log file upload produces results
  - .txt file upload produces results
  - unsupported extension (.csv) returns 422
  - no extension returns 422
  - empty file returns 422
  - file exceeding size limit returns 413

Response structure
  - total_errors matches len(errors)
  - each error has all required fields
  - fingerprints are 16-char lowercase hex
  - first_seen / last_seen are ISO strings or null

Docs / OpenAPI
  - /docs returns 200
  - /openapi.json returns 200 with valid JSON containing expected paths
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from api.main import app

SAMPLE_LOG_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample.log")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helper log snippets
# ---------------------------------------------------------------------------

SINGLE_ERROR = (
    "2026-08-16 12:00:00 ERROR [payment-service] Payment failed for user 12345\n"
)

TWO_DIFFERENT_ERRORS = (
    "2026-08-16 12:00:00 ERROR [payment-service] Payment failed for user 12345\n"
    "2026-08-16 12:00:01 ERROR [payment-service] Database connection timeout attempt 1\n"
)

REPEATED_ERRORS = (
    "2026-08-16 08:00:15 ERROR [svc] Payment failed for user 10001\n"
    "2026-08-16 08:00:16 ERROR [svc] Payment failed for user 20002\n"
    "2026-08-16 08:00:17 ERROR [svc] Payment failed for user 30003\n"
)

INFO_ONLY = (
    "2026-08-16 12:00:00 INFO  [svc] Application started\n"
    "2026-08-16 12:00:01 INFO  [svc] Connected to database\n"
)

COMMENTS_ONLY = (
    "# === header comment ===\n"
    "# Contains errors and warnings\n"
)

MIXED_COMMENTS_AND_ERRORS = (
    "# --- payment-service ---\n"
    "2026-08-16 12:00:00 ERROR [payment-service] Payment failed for user 12345\n"
    "# another comment\n"
    "2026-08-16 12:00:01 INFO  [payment-service] Retry initiated\n"
)

JAVA_TRACE = (
    "2026-08-16 08:01:00,000 ERROR [order-service] Unhandled exception order_id=ORD-789\n"
    "\tat com.example.orders.OrderProcessor.process(OrderProcessor.java:142)\n"
    "Caused by: java.lang.NullPointerException: Order item list cannot be null\n"
    "\tat com.example.orders.OrderValidator.validate(OrderValidator.java:56)\n"
    "\t... 3 more\n"
)


# ===========================================================================
# GET /health
# ===========================================================================

class TestHealth:
    def test_returns_200(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_body_status_ok(self, client):
        r = client.get("/health")
        assert r.json()["status"] == "ok"

    def test_body_service_name(self, client):
        r = client.get("/health")
        assert r.json()["service"] == "loglens"


# ===========================================================================
# POST /analyze — JSON text input
# ===========================================================================

class TestAnalyzeText:
    def test_single_error_returns_200(self, client):
        r = client.post("/analyze", json={"text": SINGLE_ERROR})
        assert r.status_code == 200

    def test_single_error_total_errors(self, client):
        r = client.post("/analyze", json={"text": SINGLE_ERROR})
        body = r.json()
        assert body["total_errors"] == 1
        assert len(body["errors"]) == 1

    def test_two_different_errors_two_records(self, client):
        r = client.post("/analyze", json={"text": TWO_DIFFERENT_ERRORS})
        body = r.json()
        assert body["total_errors"] == 2

    def test_repeated_errors_aggregated(self, client):
        r = client.post("/analyze", json={"text": REPEATED_ERRORS})
        body = r.json()
        assert body["total_errors"] == 1
        assert body["errors"][0]["occurrences"] == 3

    def test_info_only_returns_zero_errors(self, client):
        r = client.post("/analyze", json={"text": INFO_ONLY})
        body = r.json()
        assert r.status_code == 200
        assert body["total_errors"] == 0
        assert body["errors"] == []

    def test_comments_ignored(self, client):
        r = client.post("/analyze", json={"text": COMMENTS_ONLY})
        body = r.json()
        assert r.status_code == 200
        assert body["total_errors"] == 0

    def test_mixed_comments_and_errors(self, client):
        """Comment lines dropped; only the ERROR line becomes a record."""
        r = client.post("/analyze", json={"text": MIXED_COMMENTS_AND_ERRORS})
        body = r.json()
        assert r.status_code == 200
        assert body["total_errors"] == 1
        assert "Payment failed" in body["errors"][0]["message"]

    def test_java_stack_trace(self, client):
        r = client.post("/analyze", json={"text": JAVA_TRACE})
        body = r.json()
        assert body["total_errors"] == 1
        assert body["errors"][0]["error_type"] == "NullPointerException"

    # ── Input validation ─────────────────────────────────────────────────────

    def test_empty_text_returns_422(self, client):
        r = client.post("/analyze", json={"text": ""})
        assert r.status_code == 422

    def test_whitespace_only_returns_422(self, client):
        r = client.post("/analyze", json={"text": "   \n  "})
        assert r.status_code == 422

    def test_missing_text_field_returns_422(self, client):
        r = client.post("/analyze", json={"log": "something"})
        assert r.status_code == 422

    def test_non_json_body_returns_422(self, client):
        r = client.post("/analyze", content="not json", headers={"Content-Type": "application/json"})
        assert r.status_code == 422

    def test_text_size_limit(self, client):
        # Generate a string slightly over 10 MB
        big = "A" * (10 * 1024 * 1024 + 1)
        r = client.post("/analyze", json={"text": big})
        assert r.status_code == 422


# ===========================================================================
# POST /analyze/upload — multipart file upload
# ===========================================================================

class TestAnalyzeUpload:
    def _upload(self, client, content: str, filename: str) -> object:
        return client.post(
            "/analyze/upload",
            files={"file": (filename, content.encode("utf-8"), "text/plain")},
        )

    def test_log_file_returns_200(self, client):
        r = self._upload(client, SINGLE_ERROR, "app.log")
        assert r.status_code == 200

    def test_txt_file_returns_200(self, client):
        r = self._upload(client, SINGLE_ERROR, "app.txt")
        assert r.status_code == 200

    def test_log_file_produces_results(self, client):
        r = self._upload(client, REPEATED_ERRORS, "app.log")
        body = r.json()
        assert body["total_errors"] == 1
        assert body["errors"][0]["occurrences"] == 3

    def test_txt_file_produces_results(self, client):
        r = self._upload(client, TWO_DIFFERENT_ERRORS, "errors.txt")
        body = r.json()
        assert body["total_errors"] == 2

    def test_sample_log_file(self, client):
        """Upload the real sample.log file and verify non-zero results."""
        with open(SAMPLE_LOG_PATH, "rb") as fh:
            r = client.post(
                "/analyze/upload",
                files={"file": ("sample.log", fh, "text/plain")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["total_errors"] > 0

    def test_sample_log_no_comment_records(self, client):
        """sample.log must produce no records whose message starts with '#'."""
        with open(SAMPLE_LOG_PATH, "rb") as fh:
            r = client.post(
                "/analyze/upload",
                files={"file": ("sample.log", fh, "text/plain")},
            )
        body = r.json()
        comment_records = [e for e in body["errors"] if e["message"].startswith("#")]
        assert len(comment_records) == 0

    def test_unsupported_extension_returns_422(self, client):
        r = self._upload(client, SINGLE_ERROR, "data.csv")
        assert r.status_code == 422

    def test_no_extension_returns_422(self, client):
        r = self._upload(client, SINGLE_ERROR, "logfile")
        assert r.status_code == 422

    def test_empty_file_returns_422(self, client):
        r = self._upload(client, "", "empty.log")
        assert r.status_code == 422

    def test_whitespace_file_returns_422(self, client):
        r = self._upload(client, "   \n  \t  ", "blank.log")
        assert r.status_code == 422

    def test_oversized_file_returns_413(self, client):
        big = "A" * (10 * 1024 * 1024 + 1)
        r = self._upload(client, big, "huge.log")
        assert r.status_code == 413


# ===========================================================================
# Response structure
# ===========================================================================

class TestResponseStructure:
    def test_total_errors_matches_errors_list(self, client):
        r = client.post("/analyze", json={"text": TWO_DIFFERENT_ERRORS})
        body = r.json()
        assert body["total_errors"] == len(body["errors"])

    def test_error_has_all_required_fields(self, client):
        r = client.post("/analyze", json={"text": SINGLE_ERROR})
        err = r.json()["errors"][0]
        required = {
            "fingerprint", "error_type", "message", "occurrences",
            "first_seen", "last_seen", "severity", "source_service", "sample_logs",
        }
        assert required.issubset(err.keys())

    def test_fingerprint_format(self, client):
        r = client.post("/analyze", json={"text": SINGLE_ERROR})
        fp = r.json()["errors"][0]["fingerprint"]
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)

    def test_severity_present(self, client):
        r = client.post("/analyze", json={"text": SINGLE_ERROR})
        assert r.json()["errors"][0]["severity"] == "ERROR"

    def test_source_service_populated(self, client):
        r = client.post("/analyze", json={"text": SINGLE_ERROR})
        assert r.json()["errors"][0]["source_service"] == "payment-service"

    def test_first_seen_is_iso_string_or_null(self, client):
        r = client.post("/analyze", json={"text": SINGLE_ERROR})
        fs = r.json()["errors"][0]["first_seen"]
        # Must be a non-empty ISO string (the test line has a timestamp)
        assert isinstance(fs, str) and len(fs) > 0

    def test_last_seen_gte_first_seen(self, client):
        r = client.post("/analyze", json={"text": REPEATED_ERRORS})
        err = r.json()["errors"][0]
        assert err["first_seen"] <= err["last_seen"]

    def test_sample_logs_is_list(self, client):
        r = client.post("/analyze", json={"text": SINGLE_ERROR})
        assert isinstance(r.json()["errors"][0]["sample_logs"], list)

    def test_occurrences_correct(self, client):
        r = client.post("/analyze", json={"text": REPEATED_ERRORS})
        assert r.json()["errors"][0]["occurrences"] == 3

    def test_info_not_in_errors(self, client):
        text = SINGLE_ERROR + INFO_ONLY
        r = client.post("/analyze", json={"text": text})
        for err in r.json()["errors"]:
            assert err["severity"] not in ("INFO", "DEBUG")


# ===========================================================================
# OpenAPI / docs endpoints
# ===========================================================================

class TestDocs:
    def test_docs_returns_200(self, client):
        r = client.get("/docs")
        assert r.status_code == 200

    def test_openapi_json_returns_200(self, client):
        r = client.get("/openapi.json")
        assert r.status_code == 200

    def test_openapi_json_contains_health_path(self, client):
        schema = r = client.get("/openapi.json").json()
        assert "/health" in schema["paths"]

    def test_openapi_json_contains_analyze_path(self, client):
        schema = client.get("/openapi.json").json()
        assert "/analyze" in schema["paths"]

    def test_openapi_json_contains_upload_path(self, client):
        schema = client.get("/openapi.json").json()
        assert "/analyze/upload" in schema["paths"]
