"""
Tests for loglens.pipeline.LogLensPipeline and loglens.storage.InMemoryStorage.

Coverage
--------
Basic behaviour    — empty/whitespace returns []; INFO/DEBUG skipped; single ERROR
Occurrence aggregation — repeated same-pattern errors increment occurrences
                         not create duplicate records; different errors → different records
Timestamps         — first_seen set on first call; last_seen updated; first ≤ last
Sample logs        — captured; capped at 5
Stack traces       — Java and Python end-to-end produce one record with correct error_type
                     same-pattern trace with different order IDs collapses to 1 record
Bloom integration  — count increments for errors; not for INFO
InMemoryStorage    — upsert, get, list_all, upsert-overwrites, clear, __len__
Serialization      — to_dict / to_json
process_file       — sample.log: no crash; errors found; payment errors aggregated to 1;
                     NPE ≠ CCE (separate records); all fingerprints unique; no INFO records
"""

import json
import os
import pytest
from datetime import datetime

from loglens.pipeline import LogLensPipeline
from loglens.bloom import BloomFilter
from loglens.storage.base import InMemoryStorage
from loglens.models import ErrorRecord


SAMPLE_LOG = os.path.join(os.path.dirname(__file__), "fixtures", "sample.log")


def _pipeline() -> LogLensPipeline:
    """Fresh isolated pipeline for each test."""
    return LogLensPipeline(
        storage=InMemoryStorage(),
        bloom_filter=BloomFilter(capacity=10_000, error_rate=0.01),
    )


# ===========================================================================
# Basic behaviour
# ===========================================================================

class TestBasicBehaviour:
    def test_empty_string(self):
        assert _pipeline().process("") == []

    def test_whitespace_only(self):
        assert _pipeline().process("   \n  ") == []

    def test_info_only_returns_empty(self):
        assert _pipeline().process("2026-08-16 12:00:00 INFO  [svc] All good\n") == []

    def test_debug_only_returns_empty(self):
        assert _pipeline().process("2026-08-16 12:00:00 DEBUG [svc] x=42\n") == []

    def test_single_error_one_record(self):
        r = _pipeline().process("2026-08-16 12:00:00 ERROR [svc] Payment failed for user 12345\n")
        assert len(r) == 1

    def test_error_record_fields(self):
        r = _pipeline().process("2026-08-16 12:00:00 ERROR [payment-service] Payment failed for user 12345\n")[0]
        assert len(r.fingerprint) == 16
        assert all(c in "0123456789abcdef" for c in r.fingerprint)
        assert r.severity == "ERROR"
        assert r.source_service == "payment-service"
        assert r.occurrences == 1

    def test_warn_produces_record(self):
        r = _pipeline().process("2026-08-16 12:00:00 WARN  [svc] Cache miss key=user:session:12345\n")
        assert len(r) == 1
        assert r[0].severity == "WARN"


# ===========================================================================
# Occurrence aggregation
# ===========================================================================

class TestAggregation:
    def test_same_pattern_increments_occurrences(self):
        text = (
            "2026-08-16 08:00:15 ERROR [svc] Payment failed for user 10001\n"
            "2026-08-16 08:00:16 ERROR [svc] Payment failed for user 20002\n"
            "2026-08-16 08:00:17 ERROR [svc] Payment failed for user 30003\n"
        )
        result = _pipeline().process(text)
        assert len(result) == 1
        assert result[0].occurrences == 3

    def test_different_errors_separate_records(self):
        text = (
            "2026-08-16 08:00:15 ERROR [svc] Payment failed for user 10001\n"
            "2026-08-16 08:00:16 ERROR [svc] Database connection timeout attempt 1\n"
        )
        assert len(_pipeline().process(text)) == 2

    def test_no_duplicate_fingerprints_in_storage(self):
        text = (
            "2026-08-16 08:00:15 ERROR [svc] Payment failed for user 10001\n"
            "2026-08-16 08:00:16 ERROR [svc] Payment failed for user 20002\n"
        )
        p = _pipeline()
        p.process(text)
        fps = [r.fingerprint for r in p.storage.list_all()]
        assert len(fps) == len(set(fps))


# ===========================================================================
# Timestamps
# ===========================================================================

class TestTimestamps:
    def test_first_seen_set(self):
        r = _pipeline().process("2026-08-16 08:00:15 ERROR [svc] Payment failed for user 10001\n")[0]
        assert r.first_seen is not None
        assert isinstance(r.first_seen, datetime)

    def test_last_seen_updated(self):
        text = (
            "2026-08-16 08:00:15,000 ERROR [svc] Payment failed for user 10001\n"
            "2026-08-16 08:01:00,000 ERROR [svc] Payment failed for user 20002\n"
        )
        r = _pipeline().process(text)[0]
        assert r.last_seen is not None
        assert r.last_seen >= r.first_seen

    def test_first_seen_le_last_seen(self):
        text = (
            "2026-08-16 08:00:00,000 ERROR [svc] Auth failed for session 111\n"
            "2026-08-16 09:00:00,000 ERROR [svc] Auth failed for session 222\n"
            "2026-08-16 10:00:00,000 ERROR [svc] Auth failed for session 333\n"
        )
        r = _pipeline().process(text)[0]
        assert r.first_seen <= r.last_seen


# ===========================================================================
# Sample logs
# ===========================================================================

class TestSampleLogs:
    def test_sample_logs_captured(self):
        text = (
            "2026-08-16 08:00:15 ERROR [svc] Payment failed for user 10001\n"
            "2026-08-16 08:00:16 ERROR [svc] Payment failed for user 20002\n"
        )
        r = _pipeline().process(text)[0]
        assert len(r.sample_logs) >= 1

    def test_sample_logs_capped_at_5(self):
        lines = "\n".join(
            f"2026-08-16 08:00:{i:02d} ERROR [svc] Payment failed for user {i}"
            for i in range(10)
        )
        r = _pipeline().process(lines)[0]
        assert len(r.sample_logs) <= 5


# ===========================================================================
# Stack traces end-to-end
# ===========================================================================

class TestStackTraces:
    JAVA = (
        "2026-08-16 08:01:00,000 ERROR [order-service] Unhandled exception order_id=ORD-789\n"
        "\tat com.example.orders.OrderProcessor.process(OrderProcessor.java:142)\n"
        "\tat com.example.orders.OrderController.submit(OrderController.java:87)\n"
        "Caused by: java.lang.NullPointerException: Order item list cannot be null\n"
        "\tat com.example.orders.OrderValidator.validate(OrderValidator.java:56)\n"
        "\t... 3 more\n"
    )
    JAVA2 = (
        "2026-08-16 08:02:00,000 ERROR [order-service] Unhandled exception order_id=ORD-991\n"
        "\tat com.example.orders.OrderProcessor.process(OrderProcessor.java:142)\n"
        "\tat com.example.orders.OrderController.submit(OrderController.java:87)\n"
        "Caused by: java.lang.NullPointerException: Order item list cannot be null\n"
        "\tat com.example.orders.OrderValidator.validate(OrderValidator.java:56)\n"
        "\t... 3 more\n"
    )
    PYTHON = (
        "ERROR:inventory.service:Unexpected error during inventory sync\n"
        "Traceback (most recent call last):\n"
        '  File "/app/inventory/sync.py", line 88, in run_sync\n'
        "    result = self._fetch_remote()\n"
        "json.JSONDecodeError: Expecting value: line 1 column 1 (char 0)\n"
    )

    def test_java_trace_one_record(self):
        assert len(_pipeline().process(self.JAVA)) == 1

    def test_java_trace_error_type_simple(self):
        r = _pipeline().process(self.JAVA)[0]
        assert r.error_type == "NullPointerException"

    def test_python_trace_one_record(self):
        assert len(_pipeline().process(self.PYTHON)) == 1

    def test_python_trace_error_type_simple(self):
        r = _pipeline().process(self.PYTHON)[0]
        assert r.error_type == "JSONDecodeError"

    def test_same_java_trace_different_order_ids_collapses(self):
        combined = self.JAVA + "\n" + self.JAVA2
        result = _pipeline().process(combined)
        assert len(result) == 1
        assert result[0].occurrences == 2


# ===========================================================================
# Bloom Filter integration
# ===========================================================================

class TestBloomIntegration:
    def test_bloom_count_increments_for_errors(self):
        p = _pipeline()
        p.process("2026-08-16 12:00:00 ERROR [svc] Payment failed for user 12345\n")
        assert p.bloom_filter.count >= 1

    def test_bloom_count_not_incremented_for_info(self):
        p = _pipeline()
        p.process("2026-08-16 12:00:00 INFO  [svc] All good\n")
        assert p.bloom_filter.count == 0


# ===========================================================================
# InMemoryStorage
# ===========================================================================

class TestInMemoryStorage:
    def _rec(self, fp: str = "fp1", occ: int = 1) -> ErrorRecord:
        return ErrorRecord(fingerprint=fp, error_type=None,
                           message="Error", occurrences=occ, severity="ERROR")

    def test_upsert_and_get(self):
        s = InMemoryStorage()
        r = self._rec()
        s.upsert(r)
        assert s.get("fp1") is r

    def test_get_missing_returns_none(self):
        assert InMemoryStorage().get("nope") is None

    def test_list_all_empty(self):
        assert InMemoryStorage().list_all() == []

    def test_list_all_returns_all(self):
        s = InMemoryStorage()
        for i in range(5):
            s.upsert(self._rec(fp=f"fp{i}"))
        assert len(s.list_all()) == 5

    def test_upsert_overwrites(self):
        s = InMemoryStorage()
        s.upsert(self._rec("fp1", occ=1))
        s.upsert(self._rec("fp1", occ=5))
        assert s.get("fp1").occurrences == 5

    def test_clear(self):
        s = InMemoryStorage()
        s.upsert(self._rec())
        s.clear()
        assert len(s) == 0

    def test_len(self):
        s = InMemoryStorage()
        assert len(s) == 0
        s.upsert(self._rec())
        assert len(s) == 1


# ===========================================================================
# Serialization
# ===========================================================================

class TestSerialization:
    def test_to_dict(self):
        r = _pipeline().process("2026-08-16 12:00:00 ERROR [svc] Payment failed for user 12345\n")[0]
        d = r.to_dict()
        assert isinstance(d, dict)
        assert "fingerprint" in d and "occurrences" in d and "severity" in d

    def test_to_json(self):
        r = _pipeline().process("2026-08-16 12:00:00 ERROR [svc] Payment failed for user 12345\n")[0]
        parsed = json.loads(r.to_json())
        assert parsed["severity"] == "ERROR"


# ===========================================================================
# process_file — sample log integration tests
# ===========================================================================

class TestProcessFile:
    def test_no_crash(self):
        assert isinstance(_pipeline().process_file(SAMPLE_LOG), list)

    def test_errors_found(self):
        assert len(_pipeline().process_file(SAMPLE_LOG)) > 0

    def test_payment_errors_aggregated(self):
        """Three 'Payment failed for user ...' lines → 1 record with occurrences=3."""
        result = _pipeline().process_file(SAMPLE_LOG)
        payment = [r for r in result if "Payment failed" in r.message]
        assert len(payment) == 1
        assert payment[0].occurrences == 3

    def test_npe_and_cce_are_separate(self):
        """NullPointerException and ClassCastException must be different records."""
        result = _pipeline().process_file(SAMPLE_LOG)
        npe_fps = {r.fingerprint for r in result if r.error_type == "NullPointerException"}
        cce_fps = {r.fingerprint for r in result if r.error_type == "ClassCastException"}
        assert len(npe_fps) >= 1
        assert len(cce_fps) >= 1
        assert npe_fps.isdisjoint(cce_fps)

    def test_all_fingerprints_unique(self):
        result = _pipeline().process_file(SAMPLE_LOG)
        fps = [r.fingerprint for r in result]
        assert len(fps) == len(set(fps))

    def test_no_info_or_debug_records(self):
        result = _pipeline().process_file(SAMPLE_LOG)
        for r in result:
            assert r.severity not in ("INFO", "DEBUG"), f"Unexpected record: {r}"
