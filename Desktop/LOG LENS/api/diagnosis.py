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
    impact: list[str] = Field(default_factory=list)


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


import re

_AWS_KEY_PATTERN = re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")
_BEARER_JWT_PATTERN = re.compile(r"\b(bearer\s+|jwt\s+)?[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*\b", re.IGNORECASE)
_SENSITIVE_PARAM_PATTERN = re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key|auth|credentials)\b\s*[:=]\s*['\"]?([^\s'\";]+)")
_IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


def redact_sensitive_data(text: str) -> str:
    """Redact sensitive credentials, keys, tokens, and IP addresses from log/code text."""
    if not text:
        return text
    redacted = _AWS_KEY_PATTERN.sub("[REDACTED_AWS_KEY]", text)
    redacted = _SENSITIVE_PARAM_PATTERN.sub(r"\1=[REDACTED]", redacted)
    redacted = _IP_PATTERN.sub("[REDACTED_IP]", redacted)
    return redacted


def extract_stack_trace_locations(sample_logs: list[str]) -> list[dict[str, Any]]:
    """Extract file names, line numbers, and function/class locations from sample log stack traces."""
    locations = []
    # Patterns for Java, Python, and generic stack traces
    java_pattern = re.compile(r"at\s+([\w\.\$]+)\(([\w\.-]+):(\d+)\)")
    python_pattern = re.compile(r'File\s+["\']([^"\']+)["\'],\s+line\s+(\d+)(?:,\s+in\s+(.+))?')

    for log in sample_logs:
        for line in log.splitlines():
            j_match = java_pattern.search(line)
            if j_match:
                locations.append({
                    "method": j_match.group(1),
                    "file": j_match.group(2),
                    "line": int(j_match.group(3)),
                })
                continue
            p_match = python_pattern.search(line)
            if p_match:
                locations.append({
                    "file": p_match.group(1),
                    "line": int(p_match.group(2)),
                    "function": p_match.group(3).strip() if p_match.group(3) else None,
                })
    return locations


class BedrockDiagnosisService(DiagnosisService):
    """Bedrock runtime diagnosis provider adapter."""

    def __init__(self, client: Any | None = None, model_id: str | None = None) -> None:
        self.client = client
        self.model_id = model_id or "anthropic.claude-3-5-sonnet-20241022-v2:0"

    def diagnose(self, records: list[ErrorRecord]) -> Diagnosis:
        serialized_evidence = redact_sensitive_data(serialize_evidence(records))
        if self.client is None:
            raise DiagnosisProviderUnavailableError("Bedrock diagnosis provider is not configured")

        prompt = (
            "You are an expert software developer and log diagnostic system.\n"
            "Analyze the provided log evidence and return a JSON object matching this exact schema:\n"
            "{\n"
            '  "summary": "Non-empty string describing what happened in plain English",\n'
            '  "root_cause": "Non-empty string describing why it happened",\n'
            '  "confidence": Float between 0.0 and 1.0,\n'
            '  "evidence": [\n'
            '    {"fingerprint": "record fingerprint", "observation": "specific observation", "source": "error_record"}\n'
            '  ],\n'
            '  "recommendations": ["Non-empty list of actionable recommendation strings"],\n'
            '  "impact": ["Likely affected files, modules, or dependent systems"]\n'
            "}\n\n"
            "Strict rules:\n"
            "1. Output ONLY valid JSON matching the schema, with no markdown code blocks or additional text.\n"
            "2. Ensure all required fields are present.\n"
            "3. Do not invent details not supported by the evidence.\n\n"
            f"Evidence JSON:\n{serialized_evidence}\n"
        )

        try:
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1000,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
            })
            response = self.client.invoke_model(
                modelId=self.model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            response_body = json.loads(response.get("body").read())
            content = response_body.get("content", [])[0].get("text", "")
            return parse_diagnosis_output(content)
        except (MalformedDiagnosisError, InvalidEvidenceError) as exc:
            raise exc
        except Exception as exc:
            raise DiagnosisServiceError(f"Bedrock invocation failed: {exc}") from exc
