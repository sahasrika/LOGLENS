"""
LogLens Processing Pipeline.

Full processing flow for a single text input:

    raw text
        → parse          (BaseParser)
        → normalize      (normalizer.normalize)
        → fingerprint    (fingerprinter.fingerprint)
        → bloom check    (BloomFilter.might_contain)
        → aggregate      (ErrorRecord.record_occurrence)
        → store          (BaseStorage.upsert)
        → return list[ErrorRecord]

Filtering rules
---------------
  ERROR, CRITICAL, WARN, FATAL, SEVERE, UNKNOWN  →  fingerprinted and stored
  INFO, DEBUG                                     →  parsed but not stored

Design notes
------------
  - All dependencies are injected at construction time (parser, storage,
    bloom filter).  The pipeline itself is stateless with respect to config.
  - The bloom filter and storage accumulate data across successive process()
    calls.  Call storage.clear() and re-create the BloomFilter between
    independent analysis sessions if isolation is needed.
  - Files are read fully into memory.  Streaming can be added later by
    switching to a line-generator in process_file().
"""

from __future__ import annotations

import logging
from typing import Optional

from loglens.bloom import BloomFilter
from loglens.fingerprinter import fingerprint as compute_fingerprint
from loglens.models import ErrorRecord, NormalizedLog, ParsedLog
from loglens.normalizer import normalize
from loglens.parsers.base import BaseParser
from loglens.parsers.registry import get_parser_for_text
from loglens.storage.base import BaseStorage, InMemoryStorage

logger = logging.getLogger(__name__)

_ERROR_LEVELS = {"ERROR", "CRITICAL", "WARN", "WARNING", "FATAL", "SEVERE", "UNKNOWN"}
_DEFAULT_BF_CAPACITY   = 100_000
_DEFAULT_BF_ERROR_RATE = 0.01


class LogLensPipeline:
    """
    Orchestrates parsing, normalisation, fingerprinting, and storage.

    Parameters
    ----------
    parser:
        A BaseParser instance.  If None, format is auto-detected per call.
    storage:
        A BaseStorage instance.  Defaults to InMemoryStorage.
    bloom_filter:
        A BloomFilter instance.  Defaults to a new filter with MVP defaults.
    """

    def __init__(
        self,
        parser: Optional[BaseParser] = None,
        storage: Optional[BaseStorage] = None,
        bloom_filter: Optional[BloomFilter] = None,
    ) -> None:
        self._parser = parser
        self._storage = storage if storage is not None else InMemoryStorage()
        self._bloom = bloom_filter or BloomFilter(
            capacity=_DEFAULT_BF_CAPACITY,
            error_rate=_DEFAULT_BF_ERROR_RATE,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, text: str) -> list[ErrorRecord]:
        """
        Analyse *text* and return all unique ErrorRecords produced.

        Returns an empty list for empty / whitespace-only input.
        INFO and DEBUG entries are parsed but not returned.
        """
        if not text or not text.strip():
            return []

        parser = self._parser or get_parser_for_text(text)
        try:
            parsed_logs = parser.parse(text)
        except Exception as exc:          # pragma: no cover — defensive
            logger.error("Parser raised an unexpected exception: %s", exc)
            parsed_logs = []

        seen: list[str] = []             # ordered, deduplicated fingerprints

        for parsed in parsed_logs:
            self._process_entry(parsed, seen)

        return [r for fp in seen if (r := self._storage.get(fp)) is not None]

    def process_file(self, path: str) -> list[ErrorRecord]:
        """
        Read a log file and run process() on its contents.

        Attempts UTF-8 decoding first, falls back to Latin-1 so
        Windows-encoded log files don't crash the pipeline.
        """
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="latin-1") as fh:
                text = fh.read()
        return self.process(text)

    @property
    def storage(self) -> BaseStorage:
        return self._storage

    @property
    def bloom_filter(self) -> BloomFilter:
        return self._bloom

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _process_entry(self, parsed: ParsedLog, seen: list[str]) -> None:
        """Run one ParsedLog through the normalise → fingerprint → store chain."""
        level = (parsed.level or "UNKNOWN").upper()
        if level in ("INFO", "DEBUG"):
            return

        normalized: NormalizedLog = normalize(parsed)
        fp = compute_fingerprint(normalized)

        if not self._bloom.might_contain(fp):
            # Definitely new
            self._bloom.add(fp)
            record = self._new_record(fp, normalized)
            record.record_occurrence(parsed)
            self._storage.upsert(record)
            seen.append(fp)
        else:
            # Possibly seen — exact lookup
            existing = self._storage.get(fp)
            if existing is None:
                # Bloom false positive — treat as new
                record = self._new_record(fp, normalized)
                record.record_occurrence(parsed)
                self._storage.upsert(record)
            else:
                existing.record_occurrence(parsed)
                self._storage.upsert(existing)

            if fp not in seen:
                seen.append(fp)

    @staticmethod
    def _new_record(fp: str, normalized: NormalizedLog) -> ErrorRecord:
        p = normalized.parsed
        return ErrorRecord(
            fingerprint=fp,
            error_type=p.error_type,
            message=p.message or p.raw or "",
            severity=(p.level or "UNKNOWN").upper(),
            source_service=p.service,
        )
