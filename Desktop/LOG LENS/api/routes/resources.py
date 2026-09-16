"""HTTP resources exposed by the LogLens application contract."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from api.dependencies import get_application_service
from api.auth_dependencies import require_user
from api.service import (
    ApplicationService,
    DiagnosisNotConfiguredError,
    DiagnosisServiceError,
    OwnershipNotConfiguredError,
    ResourceNotFoundError,
    UploadContractError,
)

router = APIRouter()


class AnalysisStatusOut(BaseModel):
    analysis_id: str
    status: str
    created_at: str
    total_errors: int
    errors: list[dict]
    updated_at: str
    user_id: str
    source_bucket: str | None = None
    source_key: str | None = None
    error_message: str | None = None


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, le=10 * 1024 * 1024)
    content: str | None = None


class UploadContractOut(BaseModel):
    status: str
    detail: str | None = None
    filename: str | None = None
    size: int | None = None
    analysis_id: str | None = None
    bucket: str | None = None
    key: str | None = None


def _service(
    service: ApplicationService = Depends(get_application_service),
) -> ApplicationService:
    return service


@router.get("/analyses/{analysis_id}", response_model=AnalysisStatusOut)
def get_analysis(analysis_id: str, service: Annotated[ApplicationService, Depends(_service)], _user=Depends(require_user)):
    try:
        return service.get_analysis(analysis_id, user=_user)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/errors", response_model=list[dict])
def list_errors(service: Annotated[ApplicationService, Depends(_service)], _user=Depends(require_user)):
    return service.list_errors(user=_user)


@router.get("/analyses", response_model=list[dict])
def list_analyses(service: Annotated[ApplicationService, Depends(_service)], _user=Depends(require_user)):
    return service.list_analyses(user=_user)


@router.get("/errors/{fingerprint}", response_model=dict)
def get_error(fingerprint: str, service: Annotated[ApplicationService, Depends(_service)], _user=Depends(require_user)):
    try:
        return service.get_error(fingerprint, user=_user)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/uploads", response_model=UploadContractOut, status_code=501)
def upload_contract(
    request: UploadRequest,
    http_request: Request,
    service: Annotated[ApplicationService, Depends(_service)],
    _user=Depends(require_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return service.validate_upload_contract(
            request.filename,
            request.size,
            request.content,
            _user,
            http_request.headers.get("X-Request-ID"),
            idempotency_key,
        )
    except UploadContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/errors/{fingerprint}/diagnose", response_model=dict, status_code=200)
def diagnose(fingerprint: str, service: Annotated[ApplicationService, Depends(_service)], _user=Depends(require_user)):
    try:
        return service.diagnose(fingerprint, user=_user)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OwnershipNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DiagnosisNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DiagnosisServiceError as exc:
        raise HTTPException(status_code=503, detail="diagnosis provider unavailable") from exc