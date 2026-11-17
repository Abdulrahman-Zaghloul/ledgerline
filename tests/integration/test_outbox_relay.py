from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from ledger.config import Settings
from ledger.db.models import OutboxEvent
from ledger.services.payments import TRANSACTION_RECORDED, PaymentService
from ledger.workers.outbox_relay import (
    SIGNATURE_HEADER,
    LoggingPublisher,
    WebhookPublisher,
    build_publisher,
    relay_once,
    run_forever,
    verify_signature,
)
from tests.integration.conftest import ApiUser, create_user, create_wallet


class RecordingPublisher:
    def __init__(self, fail: bool = False, delay: float = 0.0) -> None:
        self.fail = fail
        self.delay = delay
        self.seen: list[int] = []
        self._lock = threading.Lock()

    def publish(self, event: OutboxEvent) -> None:
        time.sleep(self.delay)
        if self.fail:
            raise ConnectionError("receiver is down")
        with self._lock:
            self.seen.append(event.id)


def _record_deposits(session_factory: sessionmaker[Session], count: int) -> None:
    with session_factory() as session:
        owner = create_user(session)
        wallet = create_wallet(session, owner)
        for _ in range(count):
            PaymentService(session).deposit(user_id=owner.id, account_id=wallet.id, amount_minor=1)
        session.commit()


def _events(session_factory: sessionmaker[Session]) -> list[OutboxEvent]:
    with session_factory() as session:
        return list(session.scalars(select(OutboxEvent).order_by(OutboxEvent.id)))


def test_every_money_movement_writes_an_event_in_the_same_transaction(
    make_user: Callable[..., ApiUser], session_factory: sessionmaker[Session]
) -> None:
    user = make_user()
    account = user.open_account()
    transaction = user.deposit(account["id"], 700).json()

    [event] = _events(session_factory)
    assert event.event_type == TRANSACTION_RECORDED
    assert str(event.aggregate_id) == transaction["id"]
    assert event.payload["kind"] == "deposit"
    assert sorted(p["amount"] for p in event.payload["postings"]) == [-700, 700]


def test_a_refused_operation_writes_no_event(
    make_user: Callable[..., ApiUser], session_factory: sessionmaker[Session]
) -> None:
    user = make_user()
    account = user.open_account()
    assert user.withdraw(account["id"], 1).status_code == 422
    assert _events(session_factory) == []


def test_relay_publishes_each_event_once(session_factory: sessionmaker[Session]) -> None:
    _record_deposits(session_factory, 3)
    publisher = RecordingPublisher()

    first = relay_once(session_factory, publisher, batch_size=10, max_attempts=3)
    second = relay_once(session_factory, publisher, batch_size=10, max_attempts=3)

    assert (first.published, second.claimed) == (3, 0)
    assert len(publisher.seen) == 3
    assert all(e.published_at is not None for e in _events(session_factory))


def test_failed_deliveries_back_off_then_dead_letter(
    session_factory: sessionmaker[Session],
) -> None:
    _record_deposits(session_factory, 1)
    failing = RecordingPublisher(fail=True)

    stats = relay_once(session_factory, failing, batch_size=10, max_attempts=2)
    [event] = _events(session_factory)
    assert stats.retried == 1
    assert event.attempts == 1
    assert event.last_error == "ConnectionError: receiver is down"
    # Backed off into the future, so an immediate second pass does not pick it up.
    assert relay_once(session_factory, failing, batch_size=10, max_attempts=2).claimed == 0

    with session_factory() as session:  # fast-forward past the backoff
        session.execute(text("UPDATE outbox_events SET next_attempt_at = now()"))
        session.commit()
    stats = relay_once(session_factory, failing, batch_size=10, max_attempts=2)
    [event] = _events(session_factory)
    assert stats.dead_lettered == 1
    assert event.dead_lettered_at is not None
    assert event.published_at is None


def test_parallel_relays_never_deliver_the_same_event_twice(
    session_factory: sessionmaker[Session],
) -> None:
    _record_deposits(session_factory, 20)
    publisher = RecordingPublisher(delay=0.01)

    def drain() -> None:
        while relay_once(session_factory, publisher, batch_size=3, max_attempts=3).claimed:
            pass

    workers = [threading.Thread(target=drain) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert sorted(publisher.seen) == sorted(set(publisher.seen))
    assert len(publisher.seen) == 20


def test_webhook_publisher_signs_the_exact_body(session_factory: sessionmaker[Session]) -> None:
    _record_deposits(session_factory, 1)
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    publisher = WebhookPublisher("https://hooks.example.com/ledger", "whsec", client)
    assert relay_once(session_factory, publisher, batch_size=10, max_attempts=3).published == 1

    [request] = received
    assert verify_signature("whsec", request.content, request.headers[SIGNATURE_HEADER])
    envelope = json.loads(request.content)
    assert envelope["type"] == TRANSACTION_RECORDED
    assert request.headers["X-Ledger-Event-Id"] == str(envelope["id"])


def test_webhook_errors_are_retried(session_factory: sessionmaker[Session]) -> None:
    _record_deposits(session_factory, 1)
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    publisher = WebhookPublisher("https://hooks.example.com/ledger", None, client)
    assert relay_once(session_factory, publisher, batch_size=10, max_attempts=3).retried == 1


def test_publisher_is_chosen_from_configuration(settings: Settings) -> None:
    assert isinstance(build_publisher(settings), LoggingPublisher)
    configured = settings.model_copy(update={"webhook_url": "https://hooks.example.com"})
    assert isinstance(build_publisher(configured), WebhookPublisher)


def test_worker_loop_drains_events_and_stops_on_signal(
    settings: Settings, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_deposits(session_factory, 2)
    stop = threading.Event()
    worker = threading.Thread(
        target=run_forever,
        args=(settings.model_copy(update={"outbox_poll_interval_seconds": 0.05}), stop),
    )
    worker.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if all(e.published_at for e in _events(session_factory)):
            break
        time.sleep(0.05)
    stop.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert all(e.published_at for e in _events(session_factory))
    assert uuid.UUID(str(_events(session_factory)[0].aggregate_id))
