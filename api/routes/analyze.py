"""
POST /analyze route — the core analysis endpoint.

Accepts either:
  A) JSON body:  {"text": "<raw log text>"}
  B) Multipart file upload:  field name "file", .log or .txt only

In both cases the text is forwarded to a fresh LogLensPipeline and the
resulting ErrorRecords are serialised to JSON.

The route is intentionally thin:
  - validate input
  - decode bytes → str
  - call pipeline.process()
  - serialise and return

No parsing, normalisation, fingerprinting, or business logic lives here.
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from pydantic import BaseModel, field_validator

from api.dependencies import get_application_service
from api.auth_dependencies import require_user

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 10 MB — reasonable ceiling for a local MVP
_MAX_BYTES = 10 * 1024 * 1024

_ALLOWED_EXTENSIONS = {".log", ".txt"}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class AnalyzeTextRequest(BaseModel):
    """Request body for raw-text analysis."""

    text: str

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text must not be empty or whitespace-only")
        if len(v.encode("utf-8")) > _MAX_BYTES:
            raise ValueError(f"text exceeds maximum size of {_MAX_BYTES // 1024 // 1024} MB")
        return v


class ErrorRecordOut(BaseModel):
    """
    JSON representation of a single ErrorRecord.

    Mirrors ErrorRecord.to_dict() but typed so FastAPI can generate an
    accurate OpenAPI schema without coupling to the dataclass directly.
    """

    fingerprint: str
    error_type: Optional[str]
    message: str
    occurrences: int
    first_seen: Optional[str]
    last_seen: Optional[str]
    severity: str
    source_service: Optional[str]
    sample_logs: list[str]


class AnalyzeResponse(BaseModel):
    """Response returned by POST /analyze."""

    analysis_id: Optional[str] = None
    status: Optional[str] = None
    total_errors: int
    errors: list[ErrorRecordOut]
    diagnosis: dict | None = None
    diagnosis_error: str | None = None
    insights: dict | None = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _run_pipeline(text: str, service=None, user=None, idempotency_key=None) -> AnalyzeResponse:
    """
    Create a fresh pipeline, process *text*, and return a serialisable response.

    A fresh pipeline per request keeps each analysis session isolated —
    no state bleeds between requests.  This is intentional for the MVP;
    the AWS phase will introduce persistent storage.
    """
    try:
        result = (service or get_application_service()).analyze(
            text, user=user, idempotency_key=idempotency_key
        )
    except Exception as exc:
        logger.error("Pipeline error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred while analysing the log data.",
        ) from exc

    error_outs = [
        ErrorRecordOut(
            **error,
        )
        for error in result["errors"]
    ]

    return AnalyzeResponse(
        analysis_id=result["analysis_id"],
        status=result["status"],
        total_errors=result["total_errors"],
        errors=error_outs,
        diagnosis=result.get("diagnosis"),
        diagnosis_error=result.get("diagnosis_error"),
        insights=result.get("insights"),
    )


def _decode_bytes(raw: bytes) -> str:
    """Decode *raw* as UTF-8, falling back to Latin-1."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    summary="Analyse log text",
    description=(
        "Analyse raw log text supplied as a JSON body `{\"text\": \"...\"}` "
        "and return a structured list of unique error patterns with occurrence "
        "counts, fingerprints, severity, and sample log lines.\n\n"
        "INFO and DEBUG entries are parsed but not included in the response. "
        "Comment lines (starting with `#`) are silently ignored."
    ),
    status_code=status.HTTP_200_OK,
)
def analyze_text(
    request: AnalyzeTextRequest,
    service=Depends(get_application_service),
    _user=Depends(require_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AnalyzeResponse:
    """Analyse raw log text provided as a JSON body."""
    return _run_pipeline(request.text, service, _user, idempotency_key)


@router.post(
    "/analyze/upload",
    response_model=AnalyzeResponse,
    summary="Analyse a log file upload",
    description=(
        "Upload a `.log` or `.txt` file using `multipart/form-data` and receive "
        "a structured list of unique error patterns.\n\n"
        "Maximum file size: 10 MB. Supported extensions: `.log`, `.txt`."
    ),
    status_code=status.HTTP_200_OK,
)
async def analyze_upload(
    file: Annotated[UploadFile, File(description="A .log or .txt file to analyse")],
    service=Depends(get_application_service),
    _user=Depends(require_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AnalyzeResponse:
    """Analyse a .log or .txt file uploaded via multipart/form-data."""
    # Validate extension
    filename = file.filename or ""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unsupported file type '{ext or '(none)'}'. "
                f"Allowed extensions: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
            ),
        )

    # Read and size-check
    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {_MAX_BYTES // 1024 // 1024} MB.",
        )

    text = _decode_bytes(raw)

    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is empty or contains only whitespace.",
        )

    return _run_pipeline(text, service, _user, idempotency_key)
