"""
Parser registry — detects the dominant log format and returns the right parser.

Format detection samples the first 50 non-empty lines and counts pattern
matches.  If no format scores above 25 % of sampled lines the generic
CommonLogParser is returned (it handles all formats internally).

Adding a new format later means:
  1. Create a BaseParser subclass.
  2. Register its signature pattern here.
"""

from __future__ import annotations

import re
from typing import Optional

from loglens.parsers.base import BaseParser
from loglens.parsers.common import CommonLogParser

_SIGNATURES: list[tuple[str, re.Pattern]] = [
    ("standard", re.compile(
        r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}.*\b(DEBUG|INFO|WARN|ERROR|CRITICAL|FATAL)\b",
        re.IGNORECASE,
    )),
    ("logback", re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.*\b(DEBUG|INFO|WARN|ERROR|CRITICAL|FATAL)\b\s+[\w.]+\s+-\s+",
        re.IGNORECASE,
    )),
    ("python", re.compile(
        r"^(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL):[\w.]+:.+",
        re.IGNORECASE,
    )),
    ("syslog", re.compile(
        r"^[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\S+\s+\S+(?:\[\d+\])?:",
    )),
]

_MIN_MATCH_FRACTION = 0.25
_SAMPLE_LINES = 50


def detect_format(text: str) -> str:
    """
    Return the name of the dominant log format in *text*.

    Returns one of: ``"standard"``, ``"logback"``, ``"python"``,
    ``"syslog"``, or ``"common"`` (fallback).
    """
    lines = [l for l in text.splitlines() if l.strip()][:_SAMPLE_LINES]
    if not lines:
        return "common"

    scores: dict[str, int] = {name: 0 for name, _ in _SIGNATURES}
    for line in lines:
        for name, pattern in _SIGNATURES:
            if pattern.match(line):
                scores[name] += 1
                break

    best = max(scores, key=lambda k: scores[k])
    if scores[best] / len(lines) >= _MIN_MATCH_FRACTION:
        return best
    return "common"


def get_parser(format_name: Optional[str] = None) -> BaseParser:
    """
    Return a parser for *format_name*.

    For the MVP all formats are handled by CommonLogParser, which tries
    each format regex in priority order.  The registry exists so that
    specialised parsers can be dropped in later without touching the pipeline.
    """
    return CommonLogParser()


def get_parser_for_text(text: str) -> BaseParser:
    """Detect the format of *text* and return the appropriate parser."""
    return get_parser(detect_format(text))
