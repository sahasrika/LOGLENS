"""
Tests for loglens.fingerprinter.

Coverage
--------
Format       — 16 hex chars, lowercase
Determinism  — same input always produces the same fingerprint
Uniqueness   — different messages, levels, or error types produce different fingerprints
Pipeline     — same-pattern logs with different runtime values produce the same fingerprint
               via the full normalize→fingerprint chain
Canonical    — _build_canonical includes level, error_type, normalized_message separated by |
"""

import pytest
from loglens.fingerprinter import fingerprint, _build_canonical
from loglens.normalizer import normalize
from loglens.models import ParsedLog, NormalizedLog


def _norm(message: str, level: str = "ERROR",
          error_type: str = None, norm_msg: str = None) -> NormalizedLog:
    parsed = ParsedLog(raw=message, level=level, message=message, error_type=error_type)
    return NormalizedLog(parsed=parsed,
                         normalized_message=norm_msg if norm_msg is not None else message)


# ===========================================================================
# Format
# ===========================================================================

class TestFormat:
    def test_length_is_16(self):
        assert len(fingerprint(_norm("Payment failed for user <NUM>"))) == 16

    def test_is_hex_string(self):
        fp = fingerprint(_norm("Payment failed for user <NUM>"))
        assert all(c in "0123456789abcdef" for c in fp)

    def test_is_lowercase(self):
        fp = fingerprint(_norm("Payment failed for user <NUM>"))
        assert fp == fp.lower()


# ===========================================================================
# Determinism
# ===========================================================================

class TestDeterminism:
    def test_same_call_same_result(self):
        n = _norm("Payment failed for user <NUM>")
        assert fingerprint(n) == fingerprint(n)

    def test_repeated_calls_stable(self):
        n = _norm("Database timeout attempt <NUM>")
        fps = {fingerprint(n) for _ in range(20)}
        assert len(fps) == 1

    def test_two_identical_inputs_same_fingerprint(self):
        assert fingerprint(_norm("Payment failed for user <NUM>")) == \
               fingerprint(_norm("Payment failed for user <NUM>"))


# ===========================================================================
# Uniqueness / differentiation
# ===========================================================================

class TestUniqueness:
    def test_different_messages_differ(self):
        assert fingerprint(_norm("Payment failed for user <NUM>")) != \
               fingerprint(_norm("Database connection timeout attempt <NUM>"))

    def test_different_levels_differ(self):
        assert fingerprint(_norm("Slow query", level="WARN")) != \
               fingerprint(_norm("Slow query", level="ERROR"))

    def test_different_error_types_differ(self):
        assert fingerprint(_norm("Handler error", error_type="NullPointerException")) != \
               fingerprint(_norm("Handler error", error_type="ClassCastException"))

    def test_none_vs_present_error_type_differ(self):
        assert fingerprint(_norm("Handler error", error_type=None)) != \
               fingerprint(_norm("Handler error", error_type="RuntimeException"))


# ===========================================================================
# Full normalize → fingerprint pipeline consistency
# ===========================================================================

class TestPipelineConsistency:
    def test_same_pattern_different_user_ids(self):
        l1 = ParsedLog(raw="e1", level="ERROR", message="Payment failed for user 12345")
        l2 = ParsedLog(raw="e2", level="ERROR", message="Payment failed for user 98231")
        assert fingerprint(normalize(l1)) == fingerprint(normalize(l2))

    def test_same_pattern_different_uuids(self):
        l1 = ParsedLog(raw="e1", level="ERROR",
                       message="Auth failed session_id=550e8400-e29b-41d4-a716-446655440000")
        l2 = ParsedLog(raw="e2", level="ERROR",
                       message="Auth failed session_id=7a6f9b3c-1d2e-4f5a-8b9c-0e1f2a3b4c5d")
        assert fingerprint(normalize(l1)) == fingerprint(normalize(l2))

    def test_same_pattern_different_ips(self):
        l1 = ParsedLog(raw="e1", level="ERROR", message="Blocked request from 192.168.100.200")
        l2 = ParsedLog(raw="e2", level="ERROR", message="Blocked request from 10.0.0.55")
        assert fingerprint(normalize(l1)) == fingerprint(normalize(l2))

    def test_different_exceptions_differ(self):
        l1 = ParsedLog(raw="e1", level="ERROR",
                       message="Caused by NullPointerException in handler",
                       error_type="NullPointerException")
        l2 = ParsedLog(raw="e2", level="ERROR",
                       message="Caused by ClassCastException in handler",
                       error_type="ClassCastException")
        assert fingerprint(normalize(l1)) != fingerprint(normalize(l2))


# ===========================================================================
# Canonical string builder
# ===========================================================================

class TestCanonical:
    def test_contains_level(self):
        assert "ERROR" in _build_canonical(_norm("msg", level="ERROR"))

    def test_contains_error_type(self):
        assert "ValueError" in _build_canonical(_norm("msg", error_type="ValueError"))

    def test_contains_normalized_message(self):
        assert "Payment failed for user <NUM>" in \
               _build_canonical(_norm("Payment failed for user <NUM>"))

    def test_pipe_separated_three_parts(self):
        canon = _build_canonical(_norm("msg", level="ERROR", error_type="ValueError"))
        assert len(canon.split("|")) == 3
