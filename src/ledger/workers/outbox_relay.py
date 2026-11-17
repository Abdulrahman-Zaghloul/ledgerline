"""Outbox relay: delivers recorded events to the outside world (ADR 0005).

Delivery is *at least once*: if the process dies after a webhook succeeds but before
the event is marked published, the event is sent again. Every event carries a stable
id so receivers can ignore duplicates.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import FrameType
from typing import Protocol

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from ledger.config import Settings
from ledger.db.models import OutboxEvent
from ledger.db.session import build_engine, build_session_factory
from ledger.observability.logging import configure_logging
from ledger.observability.metrics import OUTBOX_EVENTS
from ledger.repositories.idempotency import IdempotencyRepository
from ledger.repositories.outbox import OutboxRepository

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Ledger-Signature"
PURGE_EVERY = timedelta(minutes=10)


class Publisher(Protocol):
    def publish(self, event: OutboxEvent) -> None: ...


def event_envelope(event: OutboxEvent) -> bytes:
    return json.dumps(
        {
            "id": event.id,
            "type": event.event_type,
            "created_at": event.created_at.isoformat(),
            "data": event.payload,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def sign_payload(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, header_value: str) -> bool:
    """What a webhook receiver runs. compare_digest avoids leaking the match via timing."""
    return hmac.compare_digest(sign_payload(secret, body), header_value)


class LoggingPublisher:
    def publish(self, event: OutboxEvent) -> None:
        logger.info("event published", extra={"event_id": event.id, "event_type": event.event_type})


class WebhookPublisher:
    def __init__(
        self, url: str, secret: str | None = None, client: httpx.Client | None = None
    ) -> None:
        self._url = url
        self._secret = secret
        self._client = client or httpx.Client(timeout=5.0)

    def publish(self, event: OutboxEvent) -> None:
        body = event_envelope(event)
        headers = {
            "Content-Type": "application/json",
            "X-Ledger-Event-Id": str(event.id),
            "X-Ledger-Event-Type": event.event_type,
        }
        if self._secret:
            headers[SIGNATURE_HEADER] = sign_payload(self._secret, body)
        self._client.post(self._url, content=body, headers=headers).raise_for_status()


def backoff_seconds(
    attempts: int,
    *,
    base: float = 2.0,
    cap: float = 300.0,
    jitter: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff (2s, 4s, 8s ... capped at 5 min) plus up to 10% random jitter,
    so a fleet of failed deliveries does not retry in one synchronised burst."""
    delay = min(cap, base * 2.0 ** (attempts - 1))
    return delay + delay * 0.1 * jitter()


@dataclass(frozen=True, slots=True)
class RelayStats:
    claimed: int = 0
    published: int = 0
    retried: int = 0
    dead_lettered: int = 0


def relay_once(
    session_factory: sessionmaker[Session],
    publisher: Publisher,
    *,
    batch_size: int,
    max_attempts: int,
) -> RelayStats:
    published = retried = dead = 0
    with session_factory() as session:
        events = OutboxRepository(session).claim_due(batch_size)
        for event in events:
            try:
                publisher.publish(event)
            except Exception as exc:
                event.attempts += 1
                event.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                if event.attempts >= max_attempts:
                    event.dead_lettered_at = func.now()
                    dead += 1
                    logger.error("event dead-lettered", extra={"event_id": event.id})
                else:
                    event.next_attempt_at = func.now() + timedelta(
                        seconds=backoff_seconds(event.attempts)
                    )
                    retried += 1
                    logger.warning(
                        "event delivery failed; will retry",
                        extra={"event_id": event.id, "attempts": event.attempts},
                    )
            else:
                event.published_at = func.now()
                published += 1
        session.commit()
    OUTBOX_EVENTS.labels("published").inc(published)
    OUTBOX_EVENTS.labels("retried").inc(retried)
    OUTBOX_EVENTS.labels("dead_lettered").inc(dead)
    return RelayStats(len(events), published, retried, dead)


def purge_expired_idempotency_keys(session_factory: sessionmaker[Session], ttl_hours: int) -> int:
    with session_factory() as session:
        removed = IdempotencyRepository(session).purge_created_before(
            datetime.now(UTC) - timedelta(hours=ttl_hours)
        )
        session.commit()
    return removed


def build_publisher(settings: Settings) -> Publisher:
    if settings.webhook_url:
        secret = settings.webhook_secret.get_secret_value() if settings.webhook_secret else None
        return WebhookPublisher(settings.webhook_url, secret)
    return LoggingPublisher()


def run_forever(settings: Settings, stop: threading.Event) -> None:
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    publisher = build_publisher(settings)
    last_purge = datetime.min.replace(tzinfo=UTC)
    logger.info("outbox relay started", extra={"publisher": type(publisher).__name__})
    try:
        while not stop.is_set():
            try:
                stats = relay_once(
                    session_factory,
                    publisher,
                    batch_size=settings.outbox_batch_size,
                    max_attempts=settings.outbox_max_attempts,
                )
                if datetime.now(UTC) - last_purge > PURGE_EVERY:
                    purge_expired_idempotency_keys(
                        session_factory, settings.idempotency_key_ttl_hours
                    )
                    last_purge = datetime.now(UTC)
            except Exception:
                logger.exception("relay iteration failed")
                stats = RelayStats()
            if stats.claimed < settings.outbox_batch_size:
                stop.wait(settings.outbox_poll_interval_seconds)
    finally:
        engine.dispose()
        logger.info("outbox relay stopped")


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    stop = threading.Event()

    def _request_stop(signum: int, _frame: FrameType | None) -> None:
        logger.info("shutdown requested", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    run_forever(settings, stop)


if __name__ == "__main__":
    main()
