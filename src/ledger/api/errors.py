"""Turn exceptions into RFC 9457 ``application/problem+json`` responses."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ledger.domain.errors import (
    AuthenticationFailed,
    BusinessRuleViolation,
    Conflict,
    InvalidRequest,
    LedgerError,
    NotFound,
    RateLimited,
)
from ledger.observability.logging import request_id_var

logger = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"

_STATUS_BY_CATEGORY: tuple[tuple[type[LedgerError], int], ...] = (
    (RateLimited, 429),
    (BusinessRuleViolation, 422),
    (NotFound, 404),
    (Conflict, 409),
    (AuthenticationFailed, 401),
    (InvalidRequest, 400),
)


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"/problems/{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "request_id": request_id_var.get(),
    }
    body.update(extra or {})
    return JSONResponse(body, status_code=status, headers=headers, media_type=PROBLEM_JSON)


def status_for(error: LedgerError) -> int | None:
    for category, status in _STATUS_BY_CATEGORY:
        if isinstance(error, category):
            return status
    return None


async def _handle_ledger_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, LedgerError)
    status = status_for(exc)
    if status is None:  # an uncategorised LedgerError means a bug: log it, hide internals
        logger.error("unexpected ledger error", exc_info=exc)
        return problem_response(
            request,
            status=500,
            code="internal-error",
            title="Internal error",
            detail="Something went wrong on our side",
        )
    headers: dict[str, str] = {}
    if isinstance(exc, AuthenticationFailed):
        headers["WWW-Authenticate"] = "Bearer"
    if isinstance(exc, RateLimited):
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return problem_response(
        request, status=status, code=exc.code, title=exc.title, detail=exc.detail, headers=headers
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"location": list(err.get("loc", ())), "message": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return problem_response(
        request,
        status=422,
        code="validation-error",
        title="Request validation failed",
        detail=f"{len(errors)} field(s) failed validation",
        extra={"errors": jsonable_encoder(errors)},
    )


async def _handle_http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return problem_response(
        request,
        status=exc.status_code,
        code=f"http-{exc.status_code}",
        title=str(exc.detail),
        detail=str(exc.detail),
        headers=dict(exc.headers or {}),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(LedgerError, _handle_ledger_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
