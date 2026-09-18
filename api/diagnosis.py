"""Strict, evidence-grounded diagnosis contracts and local implementation."""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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

    fingerprint: str = Field(default="", min_length=0)
    observation: str = Field(min_length=1)
    source: Literal["error_record", "log", "stack_trace", "pattern", "code"] = "error_record"
    confidence: Literal["confirmed", "likely", "possible"] = "confirmed"


class ArchitectureComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1)
    technology: str = Field(min_length=1)
    evidence: str = Field(default="")


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    error_category: str = Field(default="Uncategorized")

    what_happened: str = Field(default="")
    why_it_happened: str = Field(default="")

    evidence: list[DiagnosisEvidence] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)

    root_cause: str = Field(min_length=1)

    beginner_explanation: str = Field(default="")
    recommended_fix: str = Field(default="")
    code_improvement: str = Field(default="")
    suggested_patch: str = Field(default="")
    why_this_improves_the_code: str = Field(default="")

    prevention_steps: list[str] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    recommendations: list[str] = Field(default_factory=list)
    impact: list[str] = Field(default_factory=list)

    priority: Literal["P1", "P2", "P3", "P4"] = "P3"
    security_risk: Literal["low", "medium", "high", "critical"] = "medium"
    architecture: list[ArchitectureComponent] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_model_confidence(cls, value: Any) -> float | None:
        return coerce_confidence(value)


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
        evidence.append(
            {
                "fingerprint": record.fingerprint,
                "error_type": record.error_type,
                "message": record.message,
                "occurrences": record.occurrences,
                "first_seen": record.first_seen.isoformat()
                if record.first_seen
                else None,
                "last_seen": record.last_seen.isoformat()
                if record.last_seen
                else None,
                "severity": record.severity,
                "source_service": record.source_service,
                "sample_logs": list(record.sample_logs),
            }
        )

    return json.dumps(
        {"evidence": evidence},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_diagnosis_output(output: str | dict[str, Any]) -> Diagnosis:
    """Strictly validate provider JSON; strip markdown blocks if needed."""
    try:
        if isinstance(output, str):
            text = output.strip()

            if text.startswith("```"):
                lines = text.splitlines()

                if lines[0].startswith("```"):
                    lines = lines[1:]

                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]

                text = "\n".join(lines).strip()

            payload = json.loads(text)
        else:
            payload = output

        if not isinstance(payload, dict):
            raise MalformedDiagnosisError("diagnosis output is malformed")

        allowed = set(Diagnosis.model_fields)
        prepared = {key: value for key, value in payload.items() if key in allowed}
        prepared["confidence"] = coerce_confidence(prepared.get("confidence"))
        if prepared["confidence"] is None:
            prepared.pop("confidence", None)
        prepared["architecture"] = _coerce_architecture(prepared.get("architecture"))
        prepared["priority"] = _coerce_priority(prepared.get("priority"))
        prepared["security_risk"] = _coerce_risk(prepared.get("security_risk"))
        if prepared.get("severity"):
            prepared["severity"] = _coerce_severity_label(prepared.get("severity")) or "medium"

        return Diagnosis.model_validate(prepared)

    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise MalformedDiagnosisError("diagnosis output is malformed") from exc


class MockDiagnosisService(DiagnosisService):
    """Deterministic local provider used for contract and wiring tests."""

    def diagnose(self, records: list[ErrorRecord]) -> Diagnosis:
        context = json.loads(serialize_evidence(records))
        items = context["evidence"]

        evidence = [
            DiagnosisEvidence(
                fingerprint=item["fingerprint"],
                observation=(
                    f"Observed {item['severity']} pattern "
                    f"with {item['occurrences']} occurrence(s)"
                ),
            )
            for item in items
        ]

        return Diagnosis(
            summary=(
                f"Observed {len(items)} supplied error pattern(s); "
                "automated diagnosis is limited."
            ),
            root_cause=(
                "Insufficient evidence to establish a confirmed root cause."
            ),
            confidence=0.2,
            evidence=evidence,
            recommendations=[
                "Review the supplied error records and corresponding "
                "application context."
            ],
        )


_AWS_KEY_PATTERN = re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")

_BEARER_JWT_PATTERN = re.compile(
    r"\b(bearer\s+|jwt\s+)"
    r"(eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*)\b"
    r"|\beyJ[A-Za-z0-9-_=]{10,}\.[A-Za-z0-9-_=]{10,}\."
    r"[A-Za-z0-9-_.+/=]{10,}\b",
    re.IGNORECASE,
)

_SENSITIVE_PARAM_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key|"
    r"auth|credentials)\b\s*[:=]\s*['\"]?([^\s'\";]+)"
)

_IP_PATTERN = re.compile(
    r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
)


def redact_sensitive_data(text: str) -> str:
    """Redact sensitive credentials, keys, tokens, and IP addresses from log/code text."""
    if not text:
        return text

    redacted = _AWS_KEY_PATTERN.sub("[REDACTED_AWS_KEY]", text)
    redacted = _BEARER_JWT_PATTERN.sub("[REDACTED_TOKEN]", redacted)
    redacted = _SENSITIVE_PARAM_PATTERN.sub(
        r"\1=[REDACTED]",
        redacted,
    )
    redacted = _IP_PATTERN.sub("[REDACTED_IP]", redacted)

    return redacted


def extract_stack_trace_locations(
    sample_logs: list[str],
) -> list[dict[str, Any]]:
    """Extract file names, line numbers, and function/class locations from sample log stack traces."""

    locations = []

    java_pattern = re.compile(
        r"at\s+([\w\.\$]+)\(([\w\.-]+):(\d+)\)"
    )

    python_pattern = re.compile(
        r'File\s+["\']([^"\']+)["\'],\s+line\s+(\d+)'
        r'(?:,\s+in\s+(.+))?'
    )

    for log in sample_logs:
        for line in log.splitlines():

            j_match = java_pattern.search(line)

            if j_match:
                locations.append(
                    {
                        "method": j_match.group(1),
                        "file": j_match.group(2),
                        "line": int(j_match.group(3)),
                    }
                )
                continue

            p_match = python_pattern.search(line)

            if p_match:
                locations.append(
                    {
                        "file": p_match.group(1),
                        "line": int(p_match.group(2)),
                        "function": (
                            p_match.group(3).strip()
                            if p_match.group(3)
                            else None
                        ),
                    }
                )

    return locations


_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

_PRIORITY_BY_SEVERITY = {
    "critical": "P1",
    "high": "P2",
    "medium": "P3",
    "low": "P4",
}

_SECURITY_KEYWORDS = (
    "auth",
    "token",
    "password",
    "passwd",
    "unauthorized",
    "forbidden",
    "jwt",
    "oauth",
    "credential",
    "injection",
    "xss",
    "csrf",
    "secret",
    "permission",
    "blocked suspicious",
)

_ARCHITECTURE_SIGNATURES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("Frontend", "React", re.compile(r"\breact(?:-dom)?\b", re.I)),
    ("Frontend", "Angular", re.compile(r"\bangular\b", re.I)),
    ("Frontend", "Vue", re.compile(r"\bvue(?:[.\s]js)?\b", re.I)),
    ("Backend", "FastAPI", re.compile(r"\bfastapi\b", re.I)),
    ("Backend", "Django", re.compile(r"\bdjango\b", re.I)),
    ("Backend", "Flask", re.compile(r"\bflask\b", re.I)),
    ("Backend", "Spring", re.compile(r"\bspringframework\b|\borg\.springframework\b", re.I)),
    ("Backend", "Express", re.compile(r"\bexpress(?:js)?\b", re.I)),
    ("Backend", "Python", re.compile(r"Traceback \(most recent call last\)|File \"[^\"]+\.py\"")),
    ("Backend", "Java", re.compile(r"\bat [\w.$]+\([\w.-]+\.java:\d+\)")),
    ("API", "HTTP API", re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+/|\bstatus=\d{3}\b|/api/", re.I)),
    ("Database", "PostgreSQL", re.compile(r"\bpostgres(?:ql)?\b|\bpsycopg|\bport=5432\b", re.I)),
    ("Database", "MySQL", re.compile(r"\bmysql\b|\bpymysql\b", re.I)),
    ("Database", "MongoDB", re.compile(r"\bmongodb\b|\bpymongo\b", re.I)),
    ("Database", "Redis", re.compile(r"\bredis\b", re.I)),
    ("Authentication", "Auth service", re.compile(r"\b(authentication failed|unauthorized|token expir|authcontroller|auth-service)\b", re.I)),
    ("Infrastructure", "AWS", re.compile(r"\b(aws|dynamodb|lambda|bedrock|arn:aws)\b", re.I)),
    ("Infrastructure", "Docker", re.compile(r"\bdocker\b", re.I)),
    ("Storage", "S3", re.compile(r"\bs3://|\bamazons3\b|\bs3_bucket\b", re.I)),
    ("Networking", "Upstream / TCP", re.compile(r"\b(econnrefused|connection refused|connection timeout|status=502|status=503|unreachable)\b", re.I)),
    ("Runtime/Language", "Node.js", re.compile(r"node:internal|\bat .+\(.+\.js:\d+:\d+\)")),
    ("Runtime/Language", "Python", re.compile(r"File \"[^\"]+\.py\", line \d+")),
    ("Runtime/Language", "Java", re.compile(r"java\.(lang|util|io|sql)\.|NullPointerException|ClassCastException")),
)


def coerce_confidence(value: Any) -> float | None:
    """Parse model confidence as a 0-1 score; accept percent values."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1.0 and number <= 100.0:
            number /= 100.0
        if 0.0 <= number <= 1.0:
            return round(number, 4)
        return None
    if isinstance(value, str):
        text = value.strip().lower().replace("%", "")
        labels = {
            "confirmed": 0.9,
            "likely": 0.7,
            "possible": 0.45,
            "high": 0.85,
            "medium": 0.6,
            "low": 0.35,
        }
        if text in labels:
            return labels[text]
        try:
            return coerce_confidence(float(text))
        except ValueError:
            return None
    return None


def _coerce_severity_label(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    aliases = {
        "moderate": "medium",
        "warn": "medium",
        "warning": "medium",
        "error": "high",
        "fatal": "critical",
        "severe": "critical",
    }
    text = aliases.get(text, text)
    if text in _SEVERITY_RANK:
        return text
    return None


def _coerce_priority(value: Any) -> str:
    if value is None or value == "":
        return "P3"
    text = str(value).strip().upper()
    if text in {"P1", "P2", "P3", "P4"}:
        return text
    return "P3"


def _coerce_risk(value: Any) -> str:
    label = _coerce_severity_label(value)
    return label or "medium"


def _coerce_architecture(value: Any) -> list[dict[str, str]]:
    if not value:
        return []
    items: list[dict[str, str]] = []
    if not isinstance(value, list):
        return items
    for item in value:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        technology = str(item.get("technology") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if category and technology:
            items.append(
                {
                    "category": category,
                    "technology": technology,
                    "evidence": evidence,
                }
            )
    return items


def _record_text(record: ErrorRecord) -> str:
    parts = [
        record.message or "",
        record.error_type or "",
        record.source_service or "",
        record.severity or "",
        *list(record.sample_logs or []),
    ]
    return "\n".join(parts)


def bucket_severity(level: str | None) -> str:
    """Map log-level or diagnosis severity onto critical/high/medium/low."""
    label = _coerce_severity_label(level)
    if label:
        return label
    text = (level or "").strip().lower()
    if text in {"critical", "fatal", "severe"}:
        return "critical"
    if text in {"error", "high", "err"}:
        return "high"
    if text in {"warn", "warning", "medium", "moderate"}:
        return "medium"
    return "low"


def priority_from_severity(severity: str) -> str:
    """Reuse existing severity classification for priority — not a second model."""
    return _PRIORITY_BY_SEVERITY.get(bucket_severity(severity), "P3")


def security_risk_for(records: list[ErrorRecord], severity: str) -> str:
    """Reuse severity, elevating only when logs themselves show security signals."""
    risk = bucket_severity(severity)
    blob = "\n".join(_record_text(record) for record in records).lower()
    if any(keyword in blob for keyword in _SECURITY_KEYWORDS):
        if _SEVERITY_RANK[risk] < _SEVERITY_RANK["high"]:
            return "high"
    return risk


def detect_architecture(records: list[ErrorRecord]) -> list[ArchitectureComponent]:
    """Detect components only when log/stack-trace evidence matches a signature."""
    blob = "\n".join(_record_text(record) for record in records)
    if not blob.strip():
        return []

    found: list[ArchitectureComponent] = []
    seen: set[tuple[str, str]] = set()
    for category, technology, pattern in _ARCHITECTURE_SIGNATURES:
        match = pattern.search(blob)
        if not match:
            continue
        key = (category, technology)
        if key in seen:
            continue
        seen.add(key)
        snippet = match.group(0).strip()
        found.append(
            ArchitectureComponent(
                category=category,
                technology=technology,
                evidence=snippet[:160],
            )
        )
    return found


def classify_component(record: ErrorRecord) -> str:
    """Assign one chart bucket from detected architecture evidence."""
    order = (
        "Database",
        "Authentication",
        "API",
        "Backend",
        "Frontend",
        "Infrastructure",
        "Storage",
        "Networking",
    )
    components = detect_architecture([record])
    by_category = {item.category: item for item in components}
    for category in order:
        if category in by_category:
            return category
    if components:
        other = components[0].category
        if other not in order:
            return "Other"
        return other
    return "Other"


def derive_confidence(
    records: list[ErrorRecord],
    diagnosis: Diagnosis | None = None,
) -> float:
    """Deterministic confidence from diagnosis evidence and log signals."""
    score = 0.28
    locations = extract_stack_trace_locations(
        [_record_text(record) for record in records]
    )
    if any(record.error_type for record in records):
        score += 0.10
    if locations:
        score += min(0.16, 0.04 * len(locations))
    occurrences = sum(record.occurrences for record in records)
    if occurrences >= 2:
        score += 0.06
    if occurrences >= 5:
        score += 0.05
    if any(record.source_service for record in records):
        score += 0.05
    if any(".java:" in _record_text(record) or ".py" in _record_text(record) for record in records):
        score += 0.04

    if diagnosis is not None:
        for item in diagnosis.evidence:
            if item.confidence == "confirmed":
                score += 0.07
            elif item.confidence == "likely":
                score += 0.04
            else:
                score += 0.015
        if diagnosis.affected_files:
            score += 0.06
        root = (diagnosis.root_cause or "").strip()
        if len(root) > 40:
            score += 0.05
        lowered = root.lower()
        if any(
            phrase in lowered
            for phrase in (
                "insufficient evidence",
                "cannot determine",
                "unknown",
                "unclear",
                "limited",
            )
        ):
            score -= 0.12
        if diagnosis.limitations:
            score -= min(0.08, 0.02 * len(diagnosis.limitations))
        if diagnosis.suggested_patch:
            score += 0.04
        if diagnosis.what_happened and diagnosis.why_it_happened:
            score += 0.03

    return round(min(0.97, max(0.08, score)), 2)


def resolve_confidence(
    model_value: Any,
    records: list[ErrorRecord],
    diagnosis: Diagnosis | None = None,
) -> float:
    """Use parsed model confidence when present; always ground it in evidence."""
    derived = derive_confidence(records, diagnosis)
    parsed = coerce_confidence(model_value)
    if parsed is None:
        return derived
    blended = (0.45 * parsed) + (0.55 * derived)
    return round(min(0.97, max(0.08, blended)), 2)


def finalize_diagnosis(diagnosis: Diagnosis, records: list[ErrorRecord]) -> Diagnosis:
    """Fill confidence, priority, security risk, and architecture from evidence."""
    severity = bucket_severity(diagnosis.severity)
    confidence = resolve_confidence(diagnosis.confidence, records, diagnosis)
    architecture = detect_architecture(records)
    return diagnosis.model_copy(
        update={
            "severity": severity,
            "confidence": confidence,
            "priority": priority_from_severity(severity),
            "security_risk": security_risk_for(records, severity),
            "architecture": architecture,
        }
    )


def build_insights(
    records: list[ErrorRecord],
    diagnosis: Diagnosis | None = None,
) -> dict[str, Any]:
    """Aggregate issue statistics and architecture from real analysis records."""
    issues = []
    for record in records:
        severity = bucket_severity(record.severity)
        if diagnosis is not None:
            confidence = resolve_confidence(None, [record], diagnosis)
        else:
            confidence = derive_confidence([record], None)
        issues.append(
            {
                "fingerprint": record.fingerprint,
                "component": classify_component(record),
                "severity": severity,
                "confidence": confidence,
                "priority": priority_from_severity(severity),
                "security_risk": security_risk_for([record], severity),
            }
        )

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    risk_counts = {"critical_high": 0, "medium": 0, "low": 0}
    priority_counts = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
    chart_categories = [
        "Backend",
        "API",
        "Database",
        "Authentication",
        "Frontend",
        "Infrastructure",
        "Storage",
        "Networking",
        "Other",
    ]
    by_component: dict[str, dict[str, int]] = {
        name: {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for name in chart_categories
    }

    for issue in issues:
        counts[issue["severity"]] += 1
        priority_counts[issue["priority"]] += 1
        if issue["security_risk"] in {"critical", "high"}:
            risk_counts["critical_high"] += 1
        elif issue["security_risk"] == "medium":
            risk_counts["medium"] += 1
        else:
            risk_counts["low"] += 1
        component = issue["component"] if issue["component"] in by_component else "Other"
        by_component[component][issue["severity"]] += 1

    confidences = [issue["confidence"] for issue in issues]
    average = round(sum(confidences) / len(confidences), 2) if confidences else None
    architecture = detect_architecture(records)
    if diagnosis is not None and diagnosis.architecture:
        architecture = diagnosis.architecture

    return {
        "architecture": [item.model_dump() for item in architecture],
        "issues": issues,
        "stats": {
            "total": len(issues),
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "high_critical_risk": risk_counts["critical_high"],
            "average_confidence": average,
            "security_risk": risk_counts,
            "priority": priority_counts,
            "by_component": by_component,
        },
    }


class BedrockDiagnosisService(DiagnosisService):
    """Bedrock runtime diagnosis provider adapter."""

    def __init__(
        self,
        client: Any | None = None,
        model_id: str | None = None,
    ) -> None:
        self.client = client
        self.model_id = (
            model_id
            if model_id is not None
            else os.getenv("BEDROCK_MODEL_ID", "").strip()
        )

    def diagnose(self, records: list[ErrorRecord]) -> Diagnosis:
        if not self.model_id or not self.model_id.strip():
            raise DiagnosisProviderUnavailableError(
                "BEDROCK_MODEL_ID is missing or not configured"
            )

        if self.client is None:
            raise DiagnosisProviderUnavailableError(
                "Bedrock diagnosis provider is not configured"
            )

        serialized_evidence = redact_sensitive_data(
            serialize_evidence(records)
        )

        prompt = (
            "You are an expert software developer diagnosing a failure. "
            "Analyze only the supplied log evidence; do not infer facts "
            "from unstated files, systems, or source code.\n"
            "Return a JSON object matching this exact schema:\n"
            "{\n"
            '  "summary": "Summary of failure",\n'
            '  "severity": "low|medium|high|critical",\n'
            '  "confidence": 0.0 to 1.0,\n'
            '  "error_category": "Category name",\n'
            '  "what_happened": "Clear explanation of what happened in simple language",\n'
            '  "why_it_happened": "Technical explanation of root cause",\n'
            '  "evidence": [\n'
            '    {"fingerprint": "fp", "observation": "obs", '
            '"source": "error_record|log|stack_trace|pattern|code", '
            '"confidence": "confirmed|likely|possible"}\n'
            '  ],\n'
            '  "affected_files": ["file1", "file2"],\n'
            '  "root_cause": "Root cause summary",\n'
            '  "beginner_explanation": "Beginner friendly analogy or explanation",\n'
            '  "recommended_fix": "Steps to fix",\n'
            '  "code_improvement": "Proposed modification",\n'
            '  "suggested_patch": "Code snippet patch",\n'
            '  "why_this_improves_the_code": '
            '"Why this improves reliability/performance",\n'
            '  "prevention_steps": ["step1", "step2"],\n'
            '  "verification_steps": ["check1", "check2"],\n'
            '  "limitations": ["limitation1"],\n'
            '  "recommendations": '
            '["Non-empty list of actionable recommendation strings"],\n'
            '  "impact": '
            '["Likely affected files, modules, or dependent systems"]\n'
            "}\n\n"
            "Strict rules:\n"
            "1. Output ONLY valid JSON matching the schema, "
            "with no markdown code blocks or additional text.\n"
            "2. Do not invent evidence or claim to have inspected "
            "source code if none was provided.\n"
            "3. Include severity, root cause, evidence, likely impact, "
            "recommended actions, prevention suggestions, confidence, "
            "and limitations/uncertainty.\n"
            "4. confidence must reflect THIS evidence only: raise it when "
            "stack traces, file locations, recurrence, and a specific "
            "root cause are present; lower it when the cause is generic "
            "or evidence is thin. Never use a fixed default.\n\n"
            f"Evidence JSON:\n{serialized_evidence}\n"
        )

        try:
            messages = [{"role": "user", "content": [{"text": prompt}]}]
            if hasattr(self.client, "converse"):
                response = self.client.converse(
                    modelId=self.model_id,
                    messages=messages,
                    inferenceConfig={
                        "maxTokens": 4096,
                        "temperature": 0.1,
                    },
                )
                content = (
                    response.get("output", {})
                    .get("message", {})
                    .get("content", [])[0]
                    .get("text", "")
                )
            else:  # Compatibility for injected pre-Converse test adapters.
                response = self.client.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps({"messages": messages}),
                    contentType="application/json",
                    accept="application/json",
                )
                response_body = json.loads(response["body"].read())
                content = response_body["content"][0]["text"]

            return parse_diagnosis_output(content)

        except (
            MalformedDiagnosisError,
            InvalidEvidenceError,
        ) as exc:
            raise exc

        except Exception as exc:
            raise DiagnosisServiceError(
                f"Bedrock invocation failed: {exc}"
            ) from exc