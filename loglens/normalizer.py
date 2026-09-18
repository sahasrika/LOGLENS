"""
Log Normalizer — strips volatile tokens from log messages.

Goal
----
Two log lines that represent the *same error pattern* but differ only in
dynamic runtime values (user IDs, request IDs, timestamps, IPs, etc.)
must produce the **same** normalized_message so they will eventually share
a fingerprint.

Two log lines that represent *different* errors must produce **different**
normalized messages so they remain distinguishable.

Design rules
------------
1. Only *values* are stripped, never *type tokens*.
   - "NullPointerException" → stays  (distinguishable from "ClassCastException")
   - "user 12345"           → "user <NUM>"
   - "192.168.1.1"          → "<IP>"

2. Exception/error class names are protected via a sentinel pass before
   any substitution runs, then restored afterwards.  This guarantees that
   no substitution rule can accidentally strip a class name like
   "NullPointerException" or "ValueError".

3. Substitutions are applied in a fixed, deterministic order.

4. The function wraps the original ParsedLog in a NormalizedLog so all
   raw values remain accessible downstream.
"""

from __future__ import annotations

import re

from loglens.models import NormalizedLog, ParsedLog


# ---------------------------------------------------------------------------
# Exception / error-type token protection
# ---------------------------------------------------------------------------
# Any token that looks like an exception or error class name is temporarily
# replaced with a sentinel before substitutions run, then restored afterwards.
# This prevents rules like the named-ID or HEX_ID rules from eating them.

_EXCEPTION_TOKEN_RE = re.compile(
    r"\b((?:[a-zA-Z_][\w]*\.)*[A-Z][a-zA-Z]*"
    r"(?:Exception|Error|Fault|Failure|Warning))\b"
)


# ---------------------------------------------------------------------------
# Substitution rules  (applied in order — most-specific first)
# ---------------------------------------------------------------------------

_SUBSTITUTIONS: list[tuple[str, re.Pattern]] = [

    # ISO-8601 / common timestamps embedded in messages
    ("<TIMESTAMP>", re.compile(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
        re.IGNORECASE,
    )),

    # UUIDs
    ("<UUID>", re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    )),

    # IPv6
    ("<IP>", re.compile(
        r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
        r"|\b(?:[0-9a-fA-F]{1,4}:){1,7}:\b"
        r"|\b::(?:[0-9a-fA-F]{1,4}:){0,6}[0-9a-fA-F]{1,4}\b"
    )),

    # IPv4
    ("<IP>", re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
    )),

    # Long hex strings (≥ 8 hex chars) — trace IDs, hashes, etc.
    ("<HEX_ID>", re.compile(r"\b[0-9a-fA-F]{8,}\b")),

    # Named-ID values:  key=<alphanumeric value>
    # Only replaces the value portion that follows = or :
    ("<ID>", re.compile(
        r"(?<=[=:])([a-zA-Z0-9][a-zA-Z0-9_\-]{2,63})"
        r"(?=\s|$|[,;)\]])"
    )),

    # Pure numeric values
    ("<NUM>", re.compile(r"\b\d+\b")),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(log: ParsedLog) -> NormalizedLog:
    """
    Produce a NormalizedLog by stripping volatile tokens from the message.

    The original ParsedLog is preserved unchanged inside the returned
    NormalizedLog so all raw values remain accessible downstream.
    """
    raw_message = log.message or log.raw or ""
    return NormalizedLog(parsed=log, normalized_message=_normalize_message(raw_message))


def _normalize_message(message: str) -> str:
    """
    Apply all substitution rules to *message* and return the stable pattern.

    Called internally; exposed at module level so tests can exercise it directly.
    """
    # ── Step 1: protect exception/error-type tokens with sentinels ───────────
    protected: list[str] = []

    def _protect(m: re.Match) -> str:
        idx = len(protected)
        protected.append(m.group(0))
        return f"\x00EXC{idx}\x00"

    result = _EXCEPTION_TOKEN_RE.sub(_protect, message)

    # ── Step 2: apply volatile-token substitutions ───────────────────────────
    for placeholder, pattern in _SUBSTITUTIONS:
        result = pattern.sub(placeholder, result)

    # ── Step 3: restore protected exception tokens ───────────────────────────
    for idx, token in enumerate(protected):
        result = result.replace(f"\x00EXC{idx}\x00", token)

    # Collapse extra whitespace produced by substitutions
    result = re.sub(r"[ \t]{2,}", " ", result)
    return result.strip()
