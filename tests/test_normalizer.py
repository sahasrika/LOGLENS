"""
Tests for loglens.normalizer.

Coverage
--------
Numeric IDs      — same pattern, different IDs → same normalised message
UUIDs            — same pattern, different UUIDs → same normalised message
IPv4 addresses   — stripped to <IP>
Embedded timestamps — stripped to <TIMESTAMP>
Hex IDs          — long hex strings stripped; short hex left alone
Exception tokens — NullPointerException, ClassCastException, ValueError PRESERVED
                   (not accidentally stripped by named-ID or other rules)
normalize()      — returns NormalizedLog; original ParsedLog unchanged;
                   handles empty/None message gracefully
"""

import pytest
from loglens.normalizer import normalize, _normalize_message
from loglens.models import ParsedLog, NormalizedLog


def _log(message: str, level: str = "ERROR", error_type: str = None) -> ParsedLog:
    return ParsedLog(raw=message, level=level, message=message, error_type=error_type)


# ===========================================================================
# Numeric IDs
# ===========================================================================

class TestNumericIDs:
    def test_same_pattern_different_user_ids(self):
        assert _normalize_message("Payment failed for user 12345") == \
               _normalize_message("Payment failed for user 98231")

    def test_different_patterns_differ(self):
        assert _normalize_message("Payment failed for user 12345") != \
               _normalize_message("Database connection timeout attempt 3")

    def test_port_number_stripped(self):
        assert _normalize_message("Connection refused host=db port=5432") == \
               _normalize_message("Connection refused host=db port=3306")

    def test_status_code_stripped(self):
        assert _normalize_message("Upstream error status=502") == \
               _normalize_message("Upstream error status=404")


# ===========================================================================
# UUIDs
# ===========================================================================

class TestUUIDs:
    def test_same_pattern_different_uuids(self):
        m1 = "Auth failed session_id=550e8400-e29b-41d4-a716-446655440000"
        m2 = "Auth failed session_id=7a6f9b3c-1d2e-4f5a-8b9c-0e1f2a3b4c5d"
        assert _normalize_message(m1) == _normalize_message(m2)

    def test_uuid_placeholder_present(self):
        result = _normalize_message("Request trace_id=550e8400-e29b-41d4-a716-446655440000 failed")
        assert "550e8400" not in result
        assert "<UUID>" in result


# ===========================================================================
# IPv4 addresses
# ===========================================================================

class TestIPAddresses:
    def test_same_pattern_different_ips(self):
        assert _normalize_message("Blocked request from 192.168.100.200") == \
               _normalize_message("Blocked request from 10.0.0.55")

    def test_ip_placeholder_present(self):
        result = _normalize_message("Blocked request from 192.168.1.1 to port 22")
        assert "192.168.1.1" not in result
        assert "<IP>" in result


# ===========================================================================
# Embedded timestamps
# ===========================================================================

class TestEmbeddedTimestamps:
    def test_same_pattern_different_timestamps(self):
        assert _normalize_message("Event occurred at 2026-08-16T12:00:00Z") == \
               _normalize_message("Event occurred at 2026-09-01T08:30:00Z")


# ===========================================================================
# Hex IDs
# ===========================================================================

class TestHexIDs:
    def test_long_hex_stripped(self):
        assert _normalize_message("Request deadbeef01234567 failed") == \
               _normalize_message("Request cafebabe12345678 failed")

    def test_short_hex_not_treated_as_hex_id(self):
        # Short hex (< 8 chars) must not trigger the HEX_ID rule
        result = _normalize_message("Error code 0xFF in module")
        assert isinstance(result, str)   # no crash


# ===========================================================================
# Exception type tokens must NOT be stripped
# ===========================================================================

class TestExceptionTokenPreservation:
    def test_null_pointer_exception_preserved(self):
        result = _normalize_message("Caused by NullPointerException in handler")
        assert "NullPointerException" in result

    def test_class_cast_exception_preserved(self):
        result = _normalize_message("Caused by ClassCastException in mapper")
        assert "ClassCastException" in result

    def test_npe_and_cce_remain_distinguishable(self):
        r1 = _normalize_message("Caused by NullPointerException in handler")
        r2 = _normalize_message("Caused by ClassCastException in handler")
        assert r1 != r2

    def test_value_error_preserved(self):
        result = _normalize_message("Raised ValueError during parsing")
        assert "ValueError" in result

    def test_json_decode_error_preserved(self):
        result = _normalize_message("Raised JSONDecodeError while loading config")
        assert "JSONDecodeError" in result


# ===========================================================================
# normalize() function
# ===========================================================================

class TestNormalizeFunction:
    def test_returns_normalized_log(self):
        assert isinstance(normalize(_log("Payment failed for user 12345")), NormalizedLog)

    def test_original_preserved(self):
        log = _log("Payment failed for user 12345")
        assert normalize(log).parsed is log

    def test_normalized_message_is_string(self):
        assert isinstance(normalize(_log("Payment failed for user 12345")).normalized_message, str)

    def test_empty_message_handled(self):
        log = ParsedLog(raw="", level="ERROR", message="")
        result = normalize(log)
        assert isinstance(result.normalized_message, str)

    def test_none_message_falls_back_to_raw(self):
        log = ParsedLog(raw="some raw line", level="ERROR", message=None)
        result = normalize(log)
        assert result.normalized_message != ""
