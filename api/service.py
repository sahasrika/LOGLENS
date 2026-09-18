"""Application use cases shared by the local HTTP API and Lambda adapter."""

from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from uuid import uuid4
from hashlib import sha256
from threading import Lock

from loglens.models import ErrorRecord
from loglens.pipeline import LogLensPipeline
from loglens.storage.base import BaseStorage
from loglens.storage.ownership import AnalysisRecord, OwnershipStorage
from api.auth import AuthenticatedUser
from api.diagnosis import (
    Diagnosis,
    DiagnosisService,
    DiagnosisServiceError,
    InvalidEvidenceError,
    MalformedDiagnosisError,
    MockDiagnosisService,
    build_insights,
    finalize_diagnosis,
)


class ResourceNotFoundError(LookupError):
    """Raised when an analysis or error fingerprint does not exist."""


class DiagnosisNotConfiguredError(RuntimeError):
    """Raised because Bedrock diagnosis is intentionally a later phase."""


class UploadContractError(ValueError):
    """Raised when upload contract metadata is invalid."""


class OwnershipNotConfiguredError(RuntimeError):
    """Raised until records have an ownership field and authorization path."""


@dataclass
class Analysis:
    analysis_id: str
    status: str
    created_at: str
    errors: list[ErrorRecord]


class ApplicationService:
    """Coordinate API use cases without putting them in HTTP adapters."""

    def __init__(self, storage: BaseStorage, pipeline_factory=LogLensPipeline,
                 diagnosis_service: DiagnosisService | None = None, s3_service=None) -> None:
        self.storage = storage
        self.pipeline_factory = pipeline_factory
        self._analyses: dict[str, Analysis] = {}
        self.diagnosis_service = diagnosis_service or MockDiagnosisService()
        self.s3_service = s3_service
        self._idempotency_lock = Lock()

    def _ownership_storage(self) -> OwnershipStorage:
        if not isinstance(self.storage, OwnershipStorage):
            raise RuntimeError("ownership-aware storage is not configured")
        return self.storage

    @staticmethod
    def _analysis_id(user_id: str, idempotency_key: str | None) -> str:
        if not idempotency_key:
            return str(uuid4())
        digest = sha256(f"{user_id}\0{idempotency_key}".encode()).hexdigest()
        return f"analysis-{digest[:32]}"

    def analyze(self, text: str, request_id: str | None = None,
                user: AuthenticatedUser | None = None,
                idempotency_key: str | None = None) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must not be empty or whitespace-only")
        if len(text.encode("utf-8")) > 10 * 1024 * 1024:
            raise ValueError("text exceeds maximum size of 10 MB")
        owner_id = getattr(user, "subject", "local") if user is not None else "local"
        if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 256):
            raise ValueError("invalid idempotency key")
        storage = self._ownership_storage()
        if idempotency_key:
            existing = storage.get_analysis_by_idempotency(owner_id, idempotency_key)
            if existing is not None:
                return self.get_analysis(existing.analysis_id, user=user)
        now = datetime.now(timezone.utc).isoformat()
        analysis_id = self._analysis_id(owner_id, idempotency_key)
        analysis_record = AnalysisRecord(
            analysis_id, owner_id, "created", now, now, idempotency_key=idempotency_key
        )
        with self._idempotency_lock:
            if idempotency_key:
                existing = storage.get_analysis_by_idempotency(owner_id, idempotency_key)
                if existing is not None:
                    return self.get_analysis(existing.analysis_id, user=user)
            try:
                storage.create_analysis(analysis_record)
            except Exception:
                if idempotency_key:
                    existing = storage.get_analysis_by_idempotency(owner_id, idempotency_key)
                    if existing is not None:
                        return self.get_analysis(existing.analysis_id, user=user)
                raise
        processing = replace(
            analysis_record,
            status="processing",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        storage.update_analysis(processing, expected_status="created")
        analysis_record = processing
        diagnosis = None
        diagnosis_error = None
        try:
            records = self.pipeline_factory(storage=self.storage).process(text)
            analysis_record.error_fingerprints = [record.fingerprint for record in records]
            for record in records:
                storage.associate_error(owner_id, analysis_id, record)
            if records:
                try:
                    diagnosis = finalize_diagnosis(
                        self.diagnosis_service.diagnose(records), records
                    ).model_dump()
                except (
                    DiagnosisServiceError,
                    MalformedDiagnosisError,
                    InvalidEvidenceError,
                ):
                    diagnosis_error = (
                        "AI diagnosis is unavailable. Check AWS credentials, "
                        "Bedrock model access, region, and BEDROCK_MODEL_ID."
                    )
            completed = replace(
                analysis_record,
                status="completed",
                updated_at=datetime.now(timezone.utc).isoformat(),
                error_message=None,
            )
            analysis_record = completed
        except Exception:
            failed = replace(
                analysis_record,
                status="failed",
                error_message="analysis processing failed",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            storage.update_analysis(failed, expected_status="processing")
            raise
        diagnosis_model = None
        if diagnosis:
            diagnosis_model = Diagnosis.model_validate(diagnosis)
        return {
            "analysis_id": analysis_record.analysis_id,
            "status": analysis_record.status,
            "total_errors": len(records),
            "errors": [record.to_dict() for record in records],
            "diagnosis": diagnosis,
            "diagnosis_error": diagnosis_error,
            "insights": build_insights(records, diagnosis_model),
        }

    def get_analysis(self, analysis_id: str, user: AuthenticatedUser | None = None) -> dict:
        owner_id = getattr(user, "subject", "local") if user is not None else "local"
        analysis = self._ownership_storage().get_analysis_for_user(owner_id, analysis_id)
        if analysis is None:
            raise ResourceNotFoundError("analysis not found")
        errors = [self.storage.get(fp) for fp in (analysis.error_fingerprints or [])]
        return {
            "analysis_id": analysis.analysis_id,
            "status": analysis.status,
            "created_at": analysis.created_at,
            "updated_at": analysis.updated_at,
            "user_id": analysis.user_id,
            "source_bucket": analysis.source_bucket,
            "source_key": analysis.source_key,
            "error_message": analysis.error_message,
            "total_errors": len(errors),
            "errors": [record.to_dict() for record in errors if record],
        }

    def list_analyses(self, user: AuthenticatedUser | None = None) -> list[dict]:
        owner_id = getattr(user, "subject", "local") if user is not None else "local"
        return [asdict(item) for item in self._ownership_storage().list_analyses_for_user(owner_id)]

    def list_errors(self, user: AuthenticatedUser | None = None) -> list[dict]:
        owner_id = getattr(user, "subject", "local") if user is not None else "local"
        return [record.to_dict() for record in self._ownership_storage().list_errors_for_user(owner_id)]

    def get_error(self, fingerprint: str, user: AuthenticatedUser | None = None) -> dict:
        owner_id = getattr(user, "subject", "local") if user is not None else "local"
        record = self._ownership_storage().get_error_for_user(owner_id, fingerprint)
        if record is None and user is None:
            record = self.storage.get(fingerprint)
        if record is None:
            raise ResourceNotFoundError("error record not found")
        return record.to_dict()

    def validate_upload_contract(self, filename: str, size: int, content: bytes | str | None = None,
                                 user: AuthenticatedUser | None = None, request_id: str | None = None,
                                 idempotency_key: str | None = None) -> dict:
        if not filename or not filename.strip():
            raise UploadContractError("filename must not be empty")
        if filename.lower().rsplit(".", 1)[-1] not in {"log", "txt"}:
            raise UploadContractError("only .log and .txt files are supported")
        if size < 1:
            raise UploadContractError("upload must not be empty")
        if size > 10 * 1024 * 1024:
            raise UploadContractError("upload exceeds the 10 MB maximum")
        if content is None or self.s3_service is None or user is None:
            return {
            "status": "not_configured",
            "detail": "S3 upload orchestration is not configured in this phase.",
            "filename": filename.replace("\\", "/").rsplit("/", 1)[-1],
            "size": size,
            }
        if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 256):
            raise UploadContractError("invalid idempotency key")
        owner_id = getattr(user, "subject", "")
        if idempotency_key:
            existing = self._ownership_storage().get_analysis_by_idempotency(owner_id, idempotency_key)
            if existing is not None:
                return {
                    "analysis_id": existing.analysis_id,
                    "status": existing.status,
                    "bucket": existing.source_bucket,
                    "key": existing.source_key,
                    "size": size,
                }
        analysis_id = self._analysis_id(owner_id, idempotency_key)
        now = datetime.now(timezone.utc).isoformat()
        analysis = AnalysisRecord(
            analysis_id, owner_id, "created", now, now, idempotency_key=idempotency_key
        )
        storage = self._ownership_storage()
        storage.create_analysis(analysis)
        processing = replace(
            analysis,
            status="processing",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        storage.update_analysis(processing, expected_status="created")
        analysis = processing
        try:
            reference = self.s3_service.upload(content, filename, owner_id, analysis_id, request_id)
            analysis.source_bucket = reference.bucket
            analysis.source_key = reference.key
            completed = replace(
                analysis,
                status="completed",
                updated_at=datetime.now(timezone.utc).isoformat(),
                error_message=None,
            )
            storage.update_analysis(completed, expected_status="processing")
            analysis = completed
        except Exception:
            failed = replace(
                analysis,
                status="failed",
                error_message="upload failed",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            storage.update_analysis(failed, expected_status="processing")
            raise
        return {"analysis_id": analysis_id, "status": analysis.status,
                "bucket": reference.bucket, "key": reference.key, "size": reference.size}

    def diagnose(self, fingerprint: str, user: AuthenticatedUser | None = None) -> dict:
        if user is not None and not getattr(user, "subject", None):
            raise OwnershipNotConfiguredError("authenticated ownership is unavailable")
        owner_id = getattr(user, "subject", "local") if user is not None else "local"
        record = self._ownership_storage().get_error_for_user(owner_id, fingerprint)
        if record is None and user is None:
            record = self.storage.get(fingerprint)
        if record is None:
            raise ResourceNotFoundError("error record not found")
        try:
            diagnosis = finalize_diagnosis(
                self.diagnosis_service.diagnose([record]), [record]
            )
            return diagnosis.model_dump()
        except DiagnosisServiceError:
            raise
        except Exception as exc:
            raise DiagnosisServiceError("diagnosis provider failed") from exc