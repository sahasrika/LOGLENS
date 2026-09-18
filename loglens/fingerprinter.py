"""
Error Fingerprinter — deterministic SHA-256 fingerprints for log errors.

Algorithm
---------
Fingerprint = SHA-256( level | error_type | normalized_message )[:16]

Why these three fields?
  level              — distinguishes ERROR from WARN even for the same message
  error_type         — keeps NullPointerException ≠ ClassCastException
  normalized_message — the stable error pattern after volatile tokens are removed

Why SHA-256 truncated to 16 hex chars?
  - Deterministic across platforms and Python versions.
  - 64-bit effective hash space.  Collision probability across one million
    distinct errors is ~2.7 × 10⁻⁹ — acceptable for error aggregation.

The fingerprint is not a cryptographic identifier.  It is a stable,
human-readable key for grouping error patterns.
"""

from __future__ import annotations

import hashlib

from loglens.models import NormalizedLog

_SEP = "|"
_TRUNCATE = 16   # 64 bits from a 256-bit hash


def fingerprint(log: NormalizedLog) -> str:
    """
    Return a 16-character lowercase hex fingerprint for *log*.

    Guaranteed to be the same value for equivalent inputs across all
    calls and process restarts.
    """
    return hashlib.sha256(_build_canonical(log).encode("utf-8")).hexdigest()[:_TRUNCATE]


def _build_canonical(log: NormalizedLog) -> str:
    """
    Build the canonical string fed into SHA-256.

    Empty strings are used for absent fields so the separator structure
    stays consistent and a missing error_type never accidentally merges
    two fingerprints that should differ.
    """
    level      = (log.parsed.level      or "").upper().strip()
    error_type = (log.parsed.error_type or "").strip()
    norm_msg   = log.normalized_message.strip()
    return _SEP.join([level, error_type, norm_msg])
