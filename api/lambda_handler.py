"""AWS Lambda entry point for API Gateway-compatible requests."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Callable

from api.service import (
    ApplicationService,
    DiagnosisNotConfiguredError,
    OwnershipNotConfiguredError,
    ResourceNotFoundError,
    UploadContractError,
)
from api.auth import AuthenticationError, authorization_token
from api.auth_dependencies import get_auth_config, get_jwt_validator
from loglens.storage.factory import create_storage

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_application() -> Callable[[str, Any], dict]:
    """Build the application once per warm Lambda execution environment."""
    service = ApplicationService(storage=create_storage())

    def application(text: str, request_id: str | None = None, user: Any = None,
                    idempotency_key: str | None = None) -> dict:
        return service.analyze(
            text,
            request_id=request_id,
            user=user,
            idempotency_key=idempotency_key,
        )

    application.service = service
    return application


def lambda_handler(event: dict[str, Any], context: Any, application: Callable | None = None) -> dict:
    """Translate an API Gateway request into an application service call."""
    request_id = _request_id(event, context)
    try:
        app = application or get_application()
        user = _authorize(event, request_id)
        result, status_code = _dispatch(event, app, request_id, user)
        return _response(status_code, result, request_id)
    except (json.JSONDecodeError, TypeError):
        return _response(400, {"detail": "Invalid request"}, request_id)
    except AuthenticationError:
        return _response(401, {"detail": "Authentication required"}, request_id)
    except ResourceNotFoundError as exc:
        return _response(404, {"detail": str(exc)}, request_id)
    except DiagnosisNotConfiguredError as exc:
        return _response(501, {"detail": str(exc)}, request_id)
    except OwnershipNotConfiguredError as exc:
        return _response(503, {"detail": str(exc)}, request_id)
    except (UploadContractError, ValueError) as exc:
        return _response(422, {"detail": str(exc)}, request_id)
    except Exception:
        logger.exception("Unhandled Lambda application error")
        return _response(500, {"detail": "An unexpected internal error occurred."}, request_id)


def _dispatch(event: dict[str, Any], application: Callable, request_id: str,
              user: Any = None) -> tuple[dict, int]:
    method = event.get("httpMethod", "POST").upper()
    path = event.get("path", "/analyze")
    service = getattr(application, "service", None)
    if path == "/health" and method == "GET":
        return {"status": "ok", "service": "loglens"}, 200
    if path == "/analyze" and method == "POST":
        kwargs = {"request_id": request_id}
        if user is not None:
            kwargs["user"] = user
        headers = event.get("headers") or {}
        idempotency_key = headers.get("Idempotency-Key") or headers.get("idempotency-key")
        if idempotency_key is not None:
            kwargs["idempotency_key"] = idempotency_key
        return application(_extract_text(event), **kwargs), 200
    if service is None:
        raise RuntimeError("application service is unavailable")
    if path == "/errors" and method == "GET":
        return {"errors": service.list_errors(user=user)}, 200
    if path == "/analyses" and method == "GET":
        return {"analyses": service.list_analyses(user=user)}, 200
    if path.startswith("/analyses/") and method == "GET":
        return service.get_analysis(path.rsplit("/", 1)[-1], user=user), 200
    if path.startswith("/errors/") and path.endswith("/diagnose") and method == "POST":
        return service.diagnose(path.split("/")[-2], user=user), 200
    if path.startswith("/errors/") and method == "GET":
        return service.get_error(path.rsplit("/", 1)[-1], user=user), 200
    if path == "/uploads" and method == "POST":
        body = _extract_body(event)
        return service.validate_upload_contract(
            body["filename"], body["size"], body.get("content"), user,
            request_id,
            (event.get("headers") or {}).get("Idempotency-Key")
            or (event.get("headers") or {}).get("idempotency-key"),
        ), 201 if body.get("content") else 501
    raise ValueError("unsupported request")


def _authorize(event: dict[str, Any], request_id: str) -> Any:
    if event.get("path", "/analyze") == "/health":
        return None
    config = get_auth_config()
    if config.auth_required:
        headers = event.get("headers") or {}
        authorization = headers.get("Authorization") or headers.get("authorization")
        token = authorization_token(authorization)
        return get_jwt_validator().validate(token)
    return None


def _extract_body(event: dict[str, Any]) -> dict:
    body = event.get("body", event)
    if event.get("isBase64Encoded"):
        raise ValueError("base64-encoded requests are not supported")
    if isinstance(body, str):
        body = json.loads(body)
    if not isinstance(body, dict):
        raise ValueError("request body must be an object")
    return body


def _extract_text(event: dict[str, Any]) -> str:
    body = _extract_body(event)
    if "text" not in body:
        raise ValueError("request must contain a text field")
    return body["text"]


def _request_id(event: dict[str, Any], context: Any) -> str:
    request_context = event.get("requestContext", {})
    return str(request_context.get("requestId") or getattr(context, "aws_request_id", "unknown"))


def _response(status_code: int, body: dict, request_id: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "X-Request-ID": request_id},
        "body": json.dumps(body, separators=(",", ":"), sort_keys=True),
    }