"""
Data models for the LogLens processing pipeline.

All models are plain dataclasses — easy to serialize, test, and pass
between pipeline stages without importing any framework.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# ParsedLog
# ---------------------------------------------------------------------------

@dataclass
class ParsedLog:
    """
    A single structured log entry extracted from raw text.

    Fields that cannot be extracted from a particular log format are left
    as None rather than raising an error, so the pipeline can still process
    partially-structured logs.
    """

    # Raw, unmodified log line(s) exactly as they appeared in the input.
    raw: str

    # Parsed timestamp.  None if the format has no timestamp.
    timestamp: Optional[datetime] = None

    # Normalised log level: DEBUG / INFO / WARN / ERROR / CRITICAL / UNKNOWN
    level: Optional[str] = None

    # Service or application name extracted from the log.
    service: Optional[str] = None

    # Human-readable log message, excluding the exception / stack trace.
    message: Optional[str] = None

    # Exception or error class name (e.g. "NullPointerException", "ValueError").
    error_type: Optional[str] = None

    # Full stack trace text attached to this log entry, if present.
    stack_trace: Optional[str] = None

    # Extra structured key=value pairs or other metadata found in the line.
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary."""
        d = asdict(self)
        if self.timestamp is not None:
            d["timestamp"] = self.timestamp.isoformat()
        return d


# ---------------------------------------------------------------------------
# NormalizedLog
# ---------------------------------------------------------------------------

@dataclass
class NormalizedLog:
    """
    A ParsedLog paired with a normalised version of its message.

    Normalisation strips volatile tokens (IDs, IPs, numbers) from the
    message so that two log lines representing the *same error pattern*
    but differing only in dynamic values produce identical normalised messages.
    """

    # Original parsed log entry, unchanged.
    parsed: ParsedLog

    # Message with all volatile tokens replaced by stable placeholders.
    normalized_message: str

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary."""
        return {
            "parsed": self.parsed.to_dict(),
            "normalized_message": self.normalized_message,
        }


# ---------------------------------------------------------------------------
# ErrorRecord
# ---------------------------------------------------------------------------

@dataclass
class ErrorRecord:
    """
    Aggregated record representing a unique error pattern.

    Multiple log lines that share the same fingerprint are collapsed into
    a single ErrorRecord.  The record tracks how many times the error
    occurred, when it was first and last seen, and keeps a small sample
    of the original raw log lines for context.
    """

    # Deterministic 16-character hex fingerprint derived from the normalised
    # error characteristics (level + error_type + normalized_message).
    fingerprint: str

    # Error / exception class name, if available.
    error_type: Optional[str]

    # Representative human-readable message (from the first occurrence).
    message: str

    # Total number of times this fingerprint has been observed.
    occurrences: int = 0

    # Timestamp of the first observed occurrence.
    first_seen: Optional[datetime] = None

    # Timestamp of the most recent observed occurrence.
    last_seen: Optional[datetime] = None

    # Derived severity label — mirrors the log level of the first occurrence.
    severity: str = "UNKNOWN"

    # Service name associated with this error, if available.
    source_service: Optional[str] = None

    # Up to 5 raw log lines that contributed to this record (for display).
    sample_logs: list[str] = field(default_factory=list)

    # Maximum number of sample log lines to keep.
    _MAX_SAMPLES: int = field(default=5, init=False, repr=False, compare=False)

    def record_occurrence(self, log: ParsedLog) -> None:
        """
        Update this record with a new occurrence of the same error.

        - Increments the occurrence counter.
        - Updates last_seen.
        - Sets first_seen on the first call.
        - Appends raw log text to sample_logs up to _MAX_SAMPLES.
        """
        self.occurrences += 1

        ts = log.timestamp
        if self.first_seen is None:
            self.first_seen = ts
        if ts is not None:
            if self.last_seen is None or ts > self.last_seen:
                self.last_seen = ts

        if len(self.sample_logs) < self._MAX_SAMPLES:
            self.sample_logs.append(log.raw)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary."""
        return {
            "fingerprint": self.fingerprint,
            "error_type": self.error_type,
            "message": self.message,
            "occurrences": self.occurrences,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "severity": self.severity,
            "source_service": self.source_service,
            "sample_logs": self.sample_logs,
        }

    def to_json(self, indent: int = 2) -> str:
        """Return a formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)
