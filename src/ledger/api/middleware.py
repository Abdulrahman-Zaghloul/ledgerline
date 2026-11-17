"""Per-request context: correlation id, access log, metrics and a last-resort error guard."""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from ledger.api.errors import problem_response
from ledger.observability.logging import request_id_var
from ledger.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS

logger = logging.getLogger("ledger.access")

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller-supplied id only if it is short and safe to write into logs.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def route_template(request: Request) -> str:
    """The matched route with parameters restored, e.g. ``/v1/accounts/{account_id}``.

    Built from the path and its parameters rather than framework internals, so it is
    stable across FastAPI versions. Unmatched paths collapse to one label, so random
    404 probes cannot create unbounded metric series.
    """
    if request.scope.get("route") is None:
        return "unmatched"
    names = {str(value): name for name, value in request.path_params.items()}
    segments = request.url.path.split("/")
    return "/".join(f"{{{names[s]}}}" if s in names else s for s in segments)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("unhandled error")
                response = problem_response(
                    request,
                    status=500,
                    code="internal-error",
                    title="Internal error",
                    detail="Something went wrong on our side",
                )
            response.headers[REQUEST_ID_HEADER] = request_id
            elapsed = time.perf_counter() - started
            route = route_template(request)
            HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(elapsed)
            logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "route": route,
                    "status": response.status_code,
                    "duration_ms": round(elapsed * 1000, 2),
                },
            )
            return response
        finally:
            request_id_var.reset(token)
