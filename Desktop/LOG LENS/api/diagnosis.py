"""Strict, evidence-grounded diagnosis contracts and local implementation."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from loglens.models import ErrorRecord


class InvalidEvidenceError(ValueError):
    """Raised when diagnosis receives no valid ErrorRecord evidence."""


class MalformedDiagnosisError(ValueError):
    """Raised when provider output does not match the strict diagnosis schema."""


class DiagnosisServiceError(RuntimeError):
    """Raised when a diagnosis provider fails."""


class DiagnosisProviderUnavailableError(DiagnosisServiceError):
    """Raised when a diagnosis provider has not been configured."""


class DiagnosisEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    source: Literal["error_record"] = "error_record"


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[DiagnosisEvidence] = Field(min_length=1)
    recommendations: list[str] = Field(min_length=1)


class DiagnosisService(ABC):
    """Provider interface for structured, evidence-grounded diagnosis."""

    @abstractmethod
    def diagnose(self, records: list[ErrorRecord]) -> Diagnosis:
        """Return a validated diagnosis for supplied records."""


def serialize_evidence(records: list[ErrorRecord]) -> str:
    """Build deterministic provider input from only supplied ErrorRecords."""
    if not records or any(not isinstance(record, ErrorRecord) for record in records):
        raise InvalidEvidenceError("at least one ErrorRecord is required")
    evidence = []
    for record in sorted(records, key=lambda item: item.fingerprint):
        evidence.append({
            "fingerprint": record.fingerprint,
            "error_type": record.error_type,
            "message": record.message,
            "occurrences": record.occurrences,
            "first_seen": record.first_seen.isoformat() if record.first_seen else None,
            "last_seen": record.last_seen.isoformat() if record.last_seen else None,
            "severity": record.severity,
            "source_service": record.source_service,
            "sample_logs": list(record.sample_logs),
        })
    return json.dumps({"evidence": evidence}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def parse_diagnosis_output(output: str | dict[str, Any]) -> Diagnosis:
    """Strictly validate provider JSON; do not repair or infer missing fields."""
    try:
        payload = json.loads(output) if isinstance(output, str) else output
        return Diagnosis.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise MalformedDiagnosisError("diagnosis output is malformed") from exc


class MockDiagnosisService(DiagnosisService):
    """Deterministic local provider used for contract and wiring tests."""

    def diagnose(self, records: list[ErrorRecord]) -> Diagnosis:
        context = json.loads(serialize_evidence(records))
        items = context["evidence"]
        evidence = [DiagnosisEvidence(
            fingerprint=item["fingerprint"],
            observation=(
                f"Observed {item['severity']} pattern with {item['occurrences']} occurrence(s)"
            ),
        ) for item in items]
        return Diagnosis(
            summary=f"Observed {len(items)} supplied error pattern(s); automated diagnosis is limited.",
            root_cause="Insufficient evidence to establish a confirmed root cause.",
            confidence=0.2,
            evidence=evidence,
            recommendations=["Review the supplied error records and corresponding application context."],
        )


class BedrockDiagnosisService(DiagnosisService):
    """Future Bedrock boundary; deliberately unavailable until configured."""

    def __init__(self, client: Any | None = None) -> None:
        self.client = client

    def diagnose(self, records: list[ErrorRecord]) -> Diagnosis:
        serialize_evidence(records)
        if self.client is None:
            raise DiagnosisProviderUnavailableError("Bedrock diagnosis provider is not configured")
        raise DiagnosisProviderUnavailableError("Bedrock adapter is not enabled in this phase")