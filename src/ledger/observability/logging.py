"""Structured logging with a request id attached to every line.

In production, logs are JSON so a log platform can index and search every field.
In development, a compact human-readable format is easier on the eyes.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "request_id",
}


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


def _extra_fields(record: logging.LogRecord) -> dict[str, object]:
    return {key: value for key, value in record.__dict__.items() if key not in _STANDARD_ATTRS}


class ConsoleFormatter(logging.Formatter):
    """Human-readable lines for local development, with extra fields as key=value pairs."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-5s [%(request_id)s] %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = " ".join(f"{key}={value}" for key, value in _extra_fields(record).items())
        return f"{line} {extras}" if extras else line


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter() if json_output else ConsoleFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Our middleware writes one structured access line per request; silence the duplicate.
    logging.getLogger("uvicorn.access").disabled = True
