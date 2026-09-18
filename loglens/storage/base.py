"""
Storage abstraction layer for LogLens.

BaseStorage defines the interface all backends must implement.
InMemoryStorage is the MVP backend — a plain dict for the lifetime of the process.

Future backends (DynamoDB, etc.) will subclass BaseStorage and implement the same
three methods, so the pipeline never needs to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from loglens.models import ErrorRecord
from loglens.storage.ownership import (
    ALLOWED_ANALYSIS_TRANSITIONS,
    ANALYSIS_STATUSES,
    AnalysisRecord,
    OwnershipStorage,
)


class BaseStorage(ABC):
    """Abstract storage interface for ErrorRecord objects."""

    @abstractmethod
    def upsert(self, record: ErrorRecord) -> None:
        """Insert or replace the record keyed by its fingerprint."""
        ...

    @abstractmethod
    def get(self, fingerprint: str) -> Optional[ErrorRecord]:
        """Return the ErrorRecord for *fingerprint*, or None if not found."""
        ...

    @abstractmethod
    def list_all(self) -> list[ErrorRecord]:
        """Return all stored ErrorRecords in no guaranteed order."""
        ...


class InMemoryStorage(BaseStorage, OwnershipStorage):
    """
    Thread-unsafe in-memory storage backed by a plain dict.

    Suitable for local development and testing.  Does not persist data
    between process restarts.  Will be joined by DynamoDBStorage in Phase 3.
    """

    def __init__(self) -> None:
        self._store: dict[str, ErrorRecord] = {}
        self._analyses: dict[tuple[str, str], AnalysisRecord] = {}
        self._relationships: dict[tuple[str, str, str], ErrorRecord] = {}

    def upsert(self, record: ErrorRecord) -> None:
        self._store[record.fingerprint] = record

    def get(self, fingerprint: str) -> Optional[ErrorRecord]:
        return self._store.get(fingerprint)

    def list_all(self) -> list[ErrorRecord]:
        return list(self._store.values())

    def clear(self) -> None:
        """Remove all records.  Useful between test cases."""
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return f"InMemoryStorage(records={len(self._store)})"

    def create_analysis(self, analysis: AnalysisRecord) -> None:
        if analysis.status != "created":
            raise ValueError("new analysis must start in created status")
        self._analyses[(analysis.user_id, analysis.analysis_id)] = analysis

    def get_analysis_by_idempotency(self, user_id: str, idempotency_key: str) -> Optional[AnalysisRecord]:
        for (owner, _), analysis in self._analyses.items():
            if owner == user_id and analysis.idempotency_key == idempotency_key:
                return analysis
        return None

    def get_analysis_for_user(self, user_id: str, analysis_id: str) -> Optional[AnalysisRecord]:
        return self._analyses.get((user_id, analysis_id))

    def list_analyses_for_user(self, user_id: str) -> list[AnalysisRecord]:
        return [item for (owner, _), item in self._analyses.items() if owner == user_id]

    def update_analysis(self, analysis: AnalysisRecord, expected_status: str | None = None) -> None:
        current = self._analyses.get((analysis.user_id, analysis.analysis_id))
        if current is None:
            raise KeyError("analysis not found")
        if expected_status is not None and current.status != expected_status:
            raise ValueError("invalid analysis status transition")
        if analysis.status not in ANALYSIS_STATUSES or analysis.status not in ALLOWED_ANALYSIS_TRANSITIONS[current.status]:
            raise ValueError("invalid analysis status transition")
        self._analyses[(analysis.user_id, analysis.analysis_id)] = analysis

    def associate_error(self, user_id: str, analysis_id: str, record: ErrorRecord) -> None:
        self._relationships[(user_id, analysis_id, record.fingerprint)] = record

    def get_error_for_user(self, user_id: str, fingerprint: str) -> Optional[ErrorRecord]:
        for (owner, _, item_fingerprint), record in self._relationships.items():
            if owner == user_id and item_fingerprint == fingerprint:
                return record
        return None

    def list_errors_for_user(self, user_id: str) -> list[ErrorRecord]:
        records = {
            record.fingerprint: record
            for (owner, _, _), record in self._relationships.items()
            if owner == user_id
        }
        return list(records.values())
