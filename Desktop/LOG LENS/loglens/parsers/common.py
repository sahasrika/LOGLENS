"""
CommonLogParser — handles the most widely-used plain-text log formats.

Supported formats (tried in priority order)
-------------------------------------------
1. Logback / Log4j   2026-08-16T12:00:00.000Z ERROR c.e.SomeClass - Message
2. Standard          2026-08-16 12:00:00 ERROR [payment-service] Message
3. Python logging    ERROR:payment.service:Message
4. Syslog (RFC 3164) Aug 16 12:00:00 hostname appname[pid]: Message
5. Bare / unstructured  (fallback — never fails)

Comment lines
-------------
Any line whose first non-whitespace character is ``#`` is treated as a
comment and silently skipped.  Comment lines are never parsed, never
fingerprinted, and never stored.

Logback is tried before Standard because both share the same timestamp
prefix; the Logback pattern is more specific (requires the ' - ' separator)
and would otherwise be swallowed by the Standard regex.

Multi-line stack traces
-----------------------
Both Java and Python stack-trace formats are recognised.  Continuation
lines are attached to the previous ParsedLog entry rather than emitted
as independent entries.

Error type extraction
---------------------
Package prefixes are stripped from exception names so that the fingerprint
is stable regardless of whether the log contains the simple or
fully-qualified class name:
    java.lang.NullPointerException  →  NullPointerException
    json.JSONDecodeError            →  JSONDecodeError
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from loglens.models import ParsedLog
from loglens.parsers.base import BaseParser


# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

_TS_SPACE  = r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?)"
_TS_SYSLOG = r"([A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"
_LEVELS    = r"(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL|FATAL|TRACE|SEVERE)"

# Format 2 — Standard:  2026-08-16 12:00:00,123 ERROR [svc] Message
_RE_STANDARD = re.compile(
    r"^" + _TS_SPACE + r"\s+" + _LEVELS + r"\s+(?:\[([^\]]+)\]\s*)?(.+)$",
    re.IGNORECASE,
)

# Format 1 — Logback / Log4j:  2026-08-16T12:00:00.000Z ERROR c.e.Class - Message
_RE_LOGBACK = re.compile(
    r"^" + _TS_SPACE + r"\s+" + _LEVELS + r"\s+([\w.]+)\s+-\s+(.+)$",
    re.IGNORECASE,
)

# Format 3 — Python:  ERROR:logger.name:Message
_RE_PYTHON = re.compile(r"^" + _LEVELS + r":([^:]+):(.+)$", re.IGNORECASE)

# Format 4 — Syslog:  Aug 16 12:00:00 hostname appname[pid]: Message
_RE_SYSLOG = re.compile(
    r"^" + _TS_SYSLOG + r"\s+(\S+)\s+(\S+?)(?:\[\d+\])?:\s*(.+)$",
    re.IGNORECASE,
)

# Exception class names — fully qualified or simple
_RE_EXCEPTION_INLINE = re.compile(
    r"\b((?:[a-zA-Z_][\w.]*\.)?[A-Z][a-zA-Z]+(?:Error|Exception|Fault|Failure))\b"
)

# Stack-trace continuation patterns
_RE_JAVA_AT         = re.compile(r"^\s+at\s+[\w$.]+\(")
_RE_JAVA_CAUSED_BY  = re.compile(r"^\s*Caused by:")
_RE_JAVA_MORE       = re.compile(r"^\s+\.\.\.\s+\d+\s+more")
_RE_PY_TRACEBACK    = re.compile(r"^\s*Traceback \(most recent call last\):")
_RE_PY_FILE         = re.compile(r'^\s+File ".+", line \d+')
_RE_PY_EXCEPTION_LINE = re.compile(
    r"^([A-Z][a-zA-Z]*(?:Error|Exception|Warning|Fault|Failure)):\s*(.+)$"
)

# Metadata key=value pairs
_RE_METADATA = re.compile(r'\b([\w.-]+)=("[^"]*"|\S+)')

# ---------------------------------------------------------------------------
# Timestamp formats
# ---------------------------------------------------------------------------

_TIMESTAMP_FMTS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S,%f",
    "%Y-%m-%d %H:%M:%S",
]


def _parse_timestamp(raw_ts: str) -> Optional[datetime]:
    raw_ts = raw_ts.strip()
    for fmt in _TIMESTAMP_FMTS:
        try:
            return datetime.strptime(raw_ts, fmt)
        except ValueError:
            continue
    return None


def _parse_syslog_timestamp(raw_ts: str) -> Optional[datetime]:
    """Parse syslog timestamp by prepending the current year to avoid Python 3.15 warning."""
    year = datetime.now().year
    try:
        return datetime.strptime(f"{year} {raw_ts.strip()}", "%Y %b %d %H:%M:%S")
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_level(raw: str) -> str:
    upper = raw.upper()
    return {"WARNING": "WARN", "FATAL": "ERROR", "SEVERE": "ERROR", "TRACE": "DEBUG"}.get(upper, upper)


def _extract_metadata(message: str) -> dict:
    return {k: v.strip('"') for k, v in _RE_METADATA.findall(message)}


def _simple_name(full: str) -> str:
    """Strip package prefix: 'java.lang.NullPointerException' → 'NullPointerException'."""
    return full.rsplit(".", 1)[-1]


def _extract_error_type(message: str, extra_line: Optional[str] = None) -> Optional[str]:
    """
    Extract the simple exception/error class name from a message or trace line.
    Returns None if nothing is found.
    """
    for text in (message or "", extra_line or ""):
        m = _RE_EXCEPTION_INLINE.search(text)
        if m:
            return _simple_name(m.group(1))
        m = _RE_PY_EXCEPTION_LINE.match(text)
        if m:
            return _simple_name(m.group(1))
    return None


def _is_stack_continuation(line: str) -> bool:
    return bool(
        _RE_JAVA_AT.match(line)
        or _RE_JAVA_CAUSED_BY.match(line)
        or _RE_JAVA_MORE.match(line)
        or _RE_PY_TRACEBACK.match(line)
        or _RE_PY_FILE.match(line)
        or _RE_PY_EXCEPTION_LINE.match(line)
    )


def _rebuild(current: ParsedLog, raw_line: str) -> ParsedLog:
    """Return a new ParsedLog with raw and stack_trace extended by raw_line."""
    new_stack = (current.stack_trace + "\n" + raw_line) if current.stack_trace else raw_line
    # Try to extract error_type from the new trace line if not already set
    et = current.error_type or _extract_error_type("", raw_line)
    return ParsedLog(
        raw=current.raw + "\n" + raw_line,
        timestamp=current.timestamp,
        level=current.level,
        service=current.service,
        message=current.message,
        error_type=et,
        stack_trace=new_stack,
        metadata=current.metadata,
    )


# ---------------------------------------------------------------------------
# CommonLogParser
# ---------------------------------------------------------------------------

class CommonLogParser(BaseParser):
    """
    Parses the most common plain-text log formats.

    Tries formats in this order per line:
      Logback → Standard → Python → Syslog → Bare (fallback)

    Stack-trace continuation lines are attached to the previous entry.
    Malformed input never crashes the parser.
    """

    def parse(self, text: str) -> list[ParsedLog]:
        if not text or not text.strip():
            return []

        lines = text.splitlines()
        results: list[ParsedLog] = []
        current: Optional[ParsedLog] = None
        in_stack_trace = False

        for raw_line in lines:
            # ── blank line → flush current entry, reset stack-trace mode ─────
            if not raw_line.strip():
                in_stack_trace = False
                if current is not None:
                    results.append(current)
                    current = None
                continue

            # ── comment line (# after optional whitespace) → skip entirely ───
            # Comments are never parsed, fingerprinted, or stored.
            if raw_line.lstrip().startswith("#"):
                continue

            # ── stack-trace continuation ──────────────────────────────────────
            if current is not None and (in_stack_trace or _is_stack_continuation(raw_line)):
                in_stack_trace = True
                current = _rebuild(current, raw_line)
                continue

            # ── flush previous entry ──────────────────────────────────────────
            if current is not None:
                results.append(current)
                current = None
            in_stack_trace = False

            # ── try each format in priority order ─────────────────────────────
            current = (
                self._try_logback(raw_line)
                or self._try_standard(raw_line)
                or self._try_python(raw_line)
                or self._try_syslog(raw_line)
                or self._try_bare(raw_line)
            )

        if current is not None:
            results.append(current)

        return results

    # ------------------------------------------------------------------
    # Format-specific matchers
    # ------------------------------------------------------------------

    def _try_logback(self, line: str) -> Optional[ParsedLog]:
        m = _RE_LOGBACK.match(line)
        if not m:
            return None
        ts_raw, level_raw, service, message = m.group(1), m.group(2), m.group(3), m.group(4)
        message = message.strip()
        return ParsedLog(
            raw=line,
            timestamp=_parse_timestamp(ts_raw),
            level=_normalise_level(level_raw),
            service=service,
            message=message,
            error_type=_extract_error_type(message),
            metadata=_extract_metadata(message),
        )

    def _try_standard(self, line: str) -> Optional[ParsedLog]:
        m = _RE_STANDARD.match(line)
        if not m:
            return None
        ts_raw, level_raw, service, message = m.group(1), m.group(2), m.group(3), m.group(4)
        message = message.strip()
        return ParsedLog(
            raw=line,
            timestamp=_parse_timestamp(ts_raw),
            level=_normalise_level(level_raw),
            service=service,
            message=message,
            error_type=_extract_error_type(message),
            metadata=_extract_metadata(message),
        )

    def _try_python(self, line: str) -> Optional[ParsedLog]:
        m = _RE_PYTHON.match(line)
        if not m:
            return None
        level_raw, service, message = m.group(1), m.group(2), m.group(3)
        message = message.strip()
        return ParsedLog(
            raw=line,
            timestamp=None,
            level=_normalise_level(level_raw),
            service=service,
            message=message,
            error_type=_extract_error_type(message),
            metadata=_extract_metadata(message),
        )

    def _try_syslog(self, line: str) -> Optional[ParsedLog]:
        m = _RE_SYSLOG.match(line)
        if not m:
            return None
        ts_raw, _host, service, message = m.group(1), m.group(2), m.group(3), m.group(4)
        message = message.strip()
        upper = message.upper()
        level = "ERROR" if any(w in upper for w in ("ERROR", "FAIL", "CRIT")) \
            else "WARN" if "WARN" in upper else "INFO"
        return ParsedLog(
            raw=line,
            timestamp=_parse_syslog_timestamp(ts_raw),
            level=level,
            service=service,
            message=message,
            error_type=_extract_error_type(message),
            metadata=_extract_metadata(message),
        )

    def _try_bare(self, line: str) -> ParsedLog:
        message = line.strip()
        level = None
        m = re.search(r'\b' + _LEVELS + r'\b', message, re.IGNORECASE)
        if m:
            level = _normalise_level(m.group(1))
        return ParsedLog(
            raw=line,
            timestamp=None,
            level=level,
            service=None,
            message=message,
            error_type=_extract_error_type(message),
            metadata=_extract_metadata(message),
        )
