from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ledger.db.models import IdempotencyKey, LedgerTransaction
from ledger.workers.outbox_relay import purge_expired_idempotency_keys
from tests.integration.conftest import ApiUser


def _transaction_count(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(LedgerTransaction)) or 0


def test_a_retried_request_is_answered_from_memory_not_executed_again(
    make_user: Callable[..., ApiUser], session_factory: sessionmaker[Session]
) -> None:
    alice, bob = make_user(), make_user()
    a, b = alice.open_account(), bob.open_account()
    alice.deposit(a["id"], 10_000)
    before = _transaction_count(session_factory)

    first = alice.transfer(a["id"], b["id"], 2_500, key="pay-rent-november")
    retry = alice.transfer(a["id"], b["id"], 2_500, key="pay-rent-november")

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    assert "idempotent-replayed" not in first.headers
    assert retry.headers["idempotent-replayed"] == "true"
    assert _transaction_count(session_factory) == before + 1
    assert alice.balance(a["id"]) == 7_500


def test_reusing_a_key_for_a_different_request_is_refused(
    make_user: Callable[..., ApiUser],
) -> None:
    user = make_user()
    account = user.open_account()
    assert user.deposit(account["id"], 100, key="k1").status_code == 201

    clash = user.deposit(account["id"], 999, key="k1")

    assert clash.status_code == 422
    assert clash.json()["type"] == "/problems/idempotency-key-reused"
    assert user.balance(account["id"]) == 100


def test_keys_are_scoped_per_user(make_user: Callable[..., ApiUser]) -> None:
    alice, bob = make_user(), make_user()
    a, b = alice.open_account(), bob.open_account()
    assert alice.deposit(a["id"], 100, key="same-key").status_code == 201
    response = bob.deposit(b["id"], 100, key="same-key")
    assert response.status_code == 201
    assert "idempotent-replayed" not in response.headers


def test_a_failed_attempt_does_not_use_up_the_key(make_user: Callable[..., ApiUser]) -> None:
    alice, bob = make_user(), make_user()
    a, b = alice.open_account(), bob.open_account()

    refused = alice.transfer(a["id"], b["id"], 500, key="retry-me")
    assert refused.status_code == 422  # insufficient funds; nothing was saved

    alice.deposit(a["id"], 1_000)
    retried = alice.transfer(a["id"], b["id"], 500, key="retry-me")
    assert retried.status_code == 201
    assert "idempotent-replayed" not in retried.headers


def test_expired_keys_are_purged(
    make_user: Callable[..., ApiUser], session_factory: sessionmaker[Session]
) -> None:
    user = make_user()
    account = user.open_account()
    user.deposit(account["id"], 100, key="old")
    user.deposit(account["id"], 100, key="fresh")
    with session_factory() as session:
        session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.key == "old")
            .values(created_at=datetime.now(UTC) - timedelta(hours=25))
        )
        session.commit()

    assert purge_expired_idempotency_keys(session_factory, ttl_hours=24) == 1
    with session_factory() as session:
        assert session.scalars(select(IdempotencyKey.key)).all() == ["fresh"]
