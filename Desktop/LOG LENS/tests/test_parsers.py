"""
Tests for loglens.parsers.common.CommonLogParser and loglens.parsers.registry.

Coverage
--------
Standard format  — all fields, level normalisation, comma-milliseconds, metadata
Logback format   — timestamp, level, service, message
Python format    — level, service, message, no timestamp
Syslog format    — service, timestamp month/day
Bare format      — level detection, no timestamp
Malformed input  — empty string, whitespace, corrupted line, special chars
Java stack trace — single entry, stack_trace attached, Caused-by, error_type,
                   raw contains all lines, next entry after trace is separate
Python stack trace — single entry, stack_trace attached, error_type extracted,
                     next entry after trace is separate
Registry         — standard / python / syslog detection, empty fallback
"""

import pytest
from datetime import datetime

from loglens.parsers.common import CommonLogParser
from loglens.parsers.registry import detect_format, get_parser_for_text
from loglens.models import ParsedLog


@pytest.fixture
def parser():
    return CommonLogParser()


# ===========================================================================
# Standard format
# ===========================================================================

class TestStandardFormat:
    def test_timestamp_extracted(self, parser):
        line = "2026-08-16 12:00:00 ERROR [payment-service] Payment failed"
        log = parser.parse(line)[0]
        assert isinstance(log.timestamp, datetime)
        assert log.timestamp.year == 2026
        assert log.timestamp.month == 8
        assert log.timestamp.day == 16

    def test_level_extracted(self, parser):
        assert parser.parse("2026-08-16 12:00:00 ERROR [svc] Msg")[0].level == "ERROR"

    def test_service_extracted(self, parser):
        assert parser.parse("2026-08-16 12:00:00 ERROR [payment-service] Msg")[0].service == "payment-service"

    def test_message_extracted(self, parser):
        assert parser.parse("2026-08-16 12:00:00 ERROR [svc] Payment failed")[0].message == "Payment failed"

    def test_info_level(self, parser):
        assert parser.parse("2026-08-16 12:00:00 INFO  [svc] OK")[0].level == "INFO"

    def test_warn_normalised_from_warning(self, parser):
        assert parser.parse("2026-08-16 12:00:00 WARNING [svc] Slow")[0].level == "WARN"

    def test_comma_milliseconds_parsed(self, parser):
        log = parser.parse("2026-08-16 08:00:15,300 INFO  [svc] OK")[0]
        assert log.timestamp is not None
        assert log.timestamp.microsecond == 300_000

    def test_metadata_extracted(self, parser):
        log = parser.parse("2026-08-16 12:00:00 INFO  [svc] Connected host=db-primary port=5432")[0]
        assert log.metadata.get("host") == "db-primary"
        assert log.metadata.get("port") == "5432"

    def test_raw_preserved(self, parser):
        line = "2026-08-16 12:00:00 ERROR [svc] Payment failed"
        assert parser.parse(line)[0].raw == line

    def test_multiple_lines_multiple_entries(self, parser):
        text = "2026-08-16 12:00:00 INFO  [svc] A\n2026-08-16 12:00:01 ERROR [svc] B"
        assert len(parser.parse(text)) == 2

    def test_no_service_bracket(self, parser):
        log = parser.parse("2026-08-16 12:00:00 ERROR Payment gateway timeout")[0]
        assert log.level == "ERROR"
        assert "Payment gateway timeout" in log.message


# ===========================================================================
# Logback / Log4j format
# ===========================================================================

class TestLogbackFormat:
    def test_basic_fields(self, parser):
        line = "2026-08-16T08:04:05.456Z ERROR c.e.auth.AuthController - Authentication failed"
        log = parser.parse(line)[0]
        assert log.level == "ERROR"
        assert log.service == "c.e.auth.AuthController"
        assert "Authentication failed" in log.message

    def test_timestamp_parsed(self, parser):
        line = "2026-08-16T08:04:00.000Z INFO  c.e.auth.AuthService - Initialised"
        assert parser.parse(line)[0].timestamp.year == 2026

    def test_warn_level(self, parser):
        line = "2026-08-16T08:04:01.123Z WARN  c.e.auth.TokenValidator - Token expiry"
        assert parser.parse(line)[0].level == "WARN"

    def test_not_confused_with_standard(self, parser):
        """A Logback line must not fall through to Standard and lose its service."""
        line = "2026-08-16T08:04:05.456Z ERROR c.e.auth.AuthController - Authentication failed"
        log = parser.parse(line)[0]
        # Service must be the dotted class, not None
        assert log.service is not None
        assert "AuthController" in log.service


# ===========================================================================
# Python logging format
# ===========================================================================

class TestPythonFormat:
    def test_basic_fields(self, parser):
        log = parser.parse("ERROR:payment.service:Payment failed")[0]
        assert log.level == "ERROR"
        assert log.service == "payment.service"
        assert log.message == "Payment failed"

    def test_no_timestamp(self, parser):
        assert parser.parse("INFO:app.main:Starting up")[0].timestamp is None

    def test_warn_level(self, parser):
        assert parser.parse("WARNING:inventory.service:Low stock")[0].level == "WARN"


# ===========================================================================
# Syslog format
# ===========================================================================

class TestSyslogFormat:
    def test_service_extracted(self, parser):
        line = "Aug 16 08:05:00 prod-host api-gateway[4421]: Request received"
        assert parser.parse(line)[0].service == "api-gateway"

    def test_timestamp_month_day(self, parser):
        line = "Aug 16 08:05:00 prod-host api-gateway[4421]: Request received"
        ts = parser.parse(line)[0].timestamp
        assert ts is not None
        assert ts.month == 8
        assert ts.day == 16


# ===========================================================================
# Bare / unstructured
# ===========================================================================

class TestBareFormat:
    def test_returns_entry(self, parser):
        log = parser.parse("Something weird happened")[0]
        assert log.message == "Something weird happened"

    def test_no_timestamp(self, parser):
        assert parser.parse("ERROR No timestamp on this error line")[0].timestamp is None

    def test_level_detected_from_bare(self, parser):
        assert parser.parse("ERROR No timestamp on this error line")[0].level == "ERROR"


# ===========================================================================
# Malformed / edge-case input
# ===========================================================================

class TestMalformedInput:
    def test_empty_string_returns_empty(self, parser):
        assert parser.parse("") == []

    def test_whitespace_only_returns_empty(self, parser):
        assert parser.parse("   \n  \t  ") == []

    def test_corrupted_line_does_not_crash(self, parser):
        logs = parser.parse("INCOMPLETE LOG ENTRY [corrupted")
        assert isinstance(logs, list)
        assert len(logs) == 1

    def test_special_chars_do_not_crash(self, parser):
        assert len(parser.parse("!!@@##$$%%")) == 1

    def test_blank_lines_only_returns_empty(self, parser):
        assert parser.parse("\n\n\n") == []


# ===========================================================================
# Java multi-line stack trace
# ===========================================================================

class TestJavaStackTrace:
    TRACE = (
        "2026-08-16 08:01:00,000 ERROR [order-service] Unhandled exception order_id=ORD-789\n"
        "\tat com.example.orders.OrderProcessor.process(OrderProcessor.java:142)\n"
        "\tat com.example.orders.OrderController.submit(OrderController.java:87)\n"
        "Caused by: java.lang.NullPointerException: Order item list cannot be null\n"
        "\tat com.example.orders.OrderValidator.validate(OrderValidator.java:56)\n"
        "\t... 3 more"
    )

    def test_single_entry(self, parser):
        assert len(parser.parse(self.TRACE)) == 1

    def test_stack_trace_attached(self, parser):
        log = parser.parse(self.TRACE)[0]
        assert log.stack_trace is not None
        assert "OrderProcessor" in log.stack_trace

    def test_caused_by_in_trace(self, parser):
        assert "Caused by" in parser.parse(self.TRACE)[0].stack_trace

    def test_error_type_is_simple_name(self, parser):
        # Must be "NullPointerException", NOT "java.lang.NullPointerException"
        assert parser.parse(self.TRACE)[0].error_type == "NullPointerException"

    def test_raw_contains_all_lines(self, parser):
        assert "OrderProcessor.java:142" in parser.parse(self.TRACE)[0].raw

    def test_entry_after_trace_is_separate(self, parser):
        text = self.TRACE + "\n\n2026-08-16 08:02:00,000 INFO  [order-service] Recovery"
        assert len(parser.parse(text)) == 2


# ===========================================================================
# Python multi-line stack trace
# ===========================================================================

class TestPythonStackTrace:
    TRACE = (
        "ERROR:inventory.service:Unexpected error during inventory sync\n"
        "Traceback (most recent call last):\n"
        '  File "/app/inventory/sync.py", line 88, in run_sync\n'
        "    result = self._fetch_remote()\n"
        '  File "/app/inventory/sync.py", line 120, in _fetch_remote\n'
        "    data = json.loads(raw_response)\n"
        "json.JSONDecodeError: Expecting value: line 1 column 1 (char 0)\n"
    )

    def test_single_entry(self, parser):
        assert len(parser.parse(self.TRACE)) == 1

    def test_stack_trace_attached(self, parser):
        assert "Traceback" in parser.parse(self.TRACE)[0].stack_trace

    def test_error_type_is_simple_name(self, parser):
        # Must be "JSONDecodeError", NOT "json.JSONDecodeError"
        assert parser.parse(self.TRACE)[0].error_type == "JSONDecodeError"

    def test_entry_after_trace_is_separate(self, parser):
        text = self.TRACE + "\nINFO:app:Done\n"
        assert len(parser.parse(text)) == 2


# ===========================================================================
# Parser registry
# ===========================================================================

class TestRegistry:
    def test_standard_detected(self):
        text = "\n".join(f"2026-08-16 12:00:0{i} ERROR [svc] Msg {i}" for i in range(10))
        assert detect_format(text) == "standard"

    def test_python_detected(self):
        text = "\n".join(f"ERROR:svc:Message {i}" for i in range(10))
        assert detect_format(text) == "python"

    def test_syslog_detected(self):
        text = "\n".join(f"Aug 16 12:00:0{i} host app[123]: Message {i}" for i in range(10))
        assert detect_format(text) == "syslog"

    def test_empty_returns_common(self):
        assert detect_format("") == "common"

    def test_get_parser_for_text_works(self):
        p = get_parser_for_text("2026-08-16 12:00:00 ERROR [svc] Msg")
        assert len(p.parse("2026-08-16 12:00:00 ERROR [svc] Msg")) == 1


# ===========================================================================
# Comment lines  (lines whose first non-whitespace char is '#')
# ===========================================================================

class TestCommentLines:
    def test_plain_comment_skipped(self, parser):
        """A bare # comment must not produce any ParsedLog."""
        assert parser.parse("# this is a comment") == []

    def test_comment_with_leading_spaces_skipped(self, parser):
        assert parser.parse("   # indented comment") == []

    def test_comment_with_tab_indent_skipped(self, parser):
        assert parser.parse("\t# tabbed comment") == []

    def test_comment_only_file_returns_empty(self, parser):
        text = (
            "# === header ===\n"
            "# Contains: INFO, WARN, ERROR\n"
            "# No real credentials.\n"
        )
        assert parser.parse(text) == []

    def test_comments_mixed_with_real_lines(self, parser):
        """Comments are dropped; real log lines are still returned."""
        text = (
            "# --- payment-service ---\n"
            "2026-08-16 12:00:00 ERROR [svc] Payment failed\n"
            "# another comment\n"
            "2026-08-16 12:00:01 INFO  [svc] Retry\n"
        )
        logs = parser.parse(text)
        assert len(logs) == 2
        assert logs[0].level == "ERROR"
        assert logs[1].level == "INFO"

    def test_hash_in_message_not_treated_as_comment(self, parser):
        """A '#' in the middle of a log message must NOT suppress the line."""
        line = "2026-08-16 12:00:00 ERROR [svc] Commit hash=abc123 failed"
        logs = parser.parse(line)
        assert len(logs) == 1
        assert "hash" in logs[0].message

    def test_comment_not_fingerprinted(self):
        """End-to-end: comment lines must not produce ErrorRecords."""
        from loglens.pipeline import LogLensPipeline
        from loglens.bloom import BloomFilter
        from loglens.storage.base import InMemoryStorage

        p = LogLensPipeline(
            storage=InMemoryStorage(),
            bloom_filter=BloomFilter(capacity=1000, error_rate=0.01),
        )
        text = (
            "# === this is a header comment ===\n"
            "# Contains errors, warnings, info\n"
            "2026-08-16 12:00:00 ERROR [svc] Real error here\n"
        )
        results = p.process(text)
        # Only the real ERROR line should produce a record
        assert len(results) == 1
        assert "Real error" in results[0].message
        # Bloom filter should have exactly one entry (the real error)
        assert p.bloom_filter.count == 1

    def test_comment_not_in_bloom_filter(self):
        """Comments must not increment the Bloom Filter count."""
        from loglens.pipeline import LogLensPipeline
        from loglens.bloom import BloomFilter
        from loglens.storage.base import InMemoryStorage

        p = LogLensPipeline(
            storage=InMemoryStorage(),
            bloom_filter=BloomFilter(capacity=1000, error_rate=0.01),
        )
        p.process("# just a comment\n# another comment\n")
        assert p.bloom_filter.count == 0
