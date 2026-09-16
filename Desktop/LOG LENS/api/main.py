"""
LogLens FastAPI application.

Entry point
-----------
Start the server locally with:

    uvicorn api.main:app --reload

Interactive docs are available at:
    http://127.0.0.1:8000/docs        (Swagger UI)
    http://127.0.0.1:8000/redoc       (ReDoc)
    http://127.0.0.1:8000/openapi.json

Design notes
------------
- The app is intentionally thin — it wires routes to the core engine and
  adds application-level concerns (CORS headers, global exception handling,
  metadata).  No business logic lives here.
- Each POST /analyze request creates a fresh LogLensPipeline so sessions
  are fully isolated.  Persistent cross-request state will be introduced
  in Phase 3 (DynamoDB).
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.auth import AuthenticationError
from api.routes.analyze import router as analyze_router
from api.routes.resources import router as resources_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="LogLens API",
    description=(
        "Analyse application and server logs.  "
        "Submit raw log text or upload a `.log` / `.txt` file and receive "
        "structured error patterns with fingerprints, occurrence counts, "
        "severity, and sample raw log lines."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response

# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch any unhandled exception and return a generic 500 without exposing
    internal details or stack traces to the client.
    """
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected internal error occurred."},
    )


@app.exception_handler(AuthenticationError)
async def authentication_exception_handler(request: Request, exc: AuthenticationError) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": "Authentication required"})

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    summary="Health check",
    description="Returns `{\"status\": \"ok\"}` when the service is running.",
    tags=["Health"],
)
def health() -> dict:
    """Service health check."""
    return {"status": "ok", "service": "loglens"}


app.include_router(analyze_router, tags=["Analysis"])
app.include_router(resources_router, tags=["Resources"])

_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/ui", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
