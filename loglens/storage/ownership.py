"""Ownership-aware storage contracts and analysis records."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from loglens.models import ErrorRecord

ANALYSIS_STATUSES = frozenset({"created", "processing", "completed", "failed"})
ALLOWED_ANALYSIS_TRANSITIONS = {
    "created": frozenset({"processing"}),
    "processing": frozenset({"completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
}


@dataclass
class AnalysisRecord:
    analysis_id: str
    user_id: str
    status: str
    created_at: str
    updated_at: str
    source_bucket: Optional[str] = None
    source_key: Optional[str] = None
    error_fingerprints: list[str] = field(default_factory=list)
    error_message: Optional[str] = None
    idempotency_key: Optional[str] = None


class OwnershipStorage(ABC):
    """Optional storage operations required by the multi-user application."""

    @abstractmethod
    def create_analysis(self, analysis: AnalysisRecord) -> None: ...

    @abstractmethod
    def get_analysis_by_idempotency(self, user_id: str, idempotency_key: str) -> Optional[AnalysisRecord]: ...

    @abstractmethod
    def get_analysis_for_user(self, user_id: str, analysis_id: str) -> Optional[AnalysisRecord]: ...

    @abstractmethod
    def list_analyses_for_user(self, user_id: str) -> list[AnalysisRecord]: ...

    @abstractmethod
    def update_analysis(self, analysis: AnalysisRecord, expected_status: str | None = None) -> None: ...

    @abstractmethod
    def associate_error(self, user_id: str, analysis_id: str, record: ErrorRecord) -> None: ...

    @abstractmethod
    def get_error_for_user(self, user_id: str, fingerprint: str) -> Optional[ErrorRecord]: ...

    @abstractmethod
    def list_errors_for_user(self, user_id: str) -> list[ErrorRecord]: ...