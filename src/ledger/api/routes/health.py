"""Liveness, readiness and metrics endpoints for the platform that runs the service."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ledger.api.schemas import HealthOut

router = APIRouter(tags=["operations"])


@router.get("/health/live", summary="Is the process running?")
def live() -> HealthOut:
    return HealthOut(status="ok")


@router.get(
    "/health/ready",
    responses={503: {"model": HealthOut}},
    summary="Can the service handle traffic (database reachable)?",
)
def ready(request: Request) -> JSONResponse:
    try:
        with request.app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
