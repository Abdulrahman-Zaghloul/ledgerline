"""Race-condition tests: many database sessions hitting the same rows at the same instant.

A ``threading.Barrier`` releases every worker at once to maximise contention. Without
row locks, the overdraft test reliably lets several withdrawals read the same balance
and all succeed; with them, exactly the affordable number succeed.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ledger.db.models import Account, LedgerTransaction
from ledger.domain.errors import InsufficientFunds
from ledger.services.idempotency import IdempotentResult, execute_idempotently
from ledger.services.payments import PaymentService
from ledger.services.reconciliation import reconcile
from tests.integration.conftest import create_user, create_wallet

pytestmark = pytest.mark.concurrency


def run_simultaneously[T](count: int, work: Callable[[int], T]) -> list[T | Exception]:
    barrier = threading.Barrier(count)

    def worker(index: int) -> T | Exception:
        barrier.wait()
        try:
            return work(index)
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(worker, range(count)))


def funded_wallets(
    session_factory: sessionmaker[Session], *balances: int
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    with session_factory() as session:
        owner = create_user(session)
        wallets = [create_wallet(session, owner) for _ in balances]
        payments = PaymentService(session)
        for wallet, balance in zip(wallets, balances, strict=True):
            if balance:
                payments.deposit(user_id=owner.id, account_id=wallet.id, amount_minor=balance)
        session.commit()
        return owner.id, [w.id for w in wallets]


def balance(session_factory: sessionmaker[Session], account_id: uuid.UUID) -> int:
    with session_factory() as session:
        value = session.scalar(select(Account.balance_minor).where(Account.id == account_id))
        return int(value or 0)


def test_parallel_withdrawals_never_overdraw(session_factory: sessionmaker[Session]) -> None:
    owner_id, [wallet_id] = funded_wallets(session_factory, 1_000)

    def withdraw(_: int) -> str:
        with session_factory() as session:
            PaymentService(session).withdraw(
                user_id=owner_id, account_id=wallet_id, amount_minor=100
            )
            session.commit()
            return "ok"

    results = run_simultaneously(20, withdraw)

    assert results.count("ok") == 10
    assert sum(isinstance(r, InsufficientFunds) for r in results) == 10
    assert balance(session_factory, wallet_id) == 0


def test_opposing_transfers_do_not_deadlock(session_factory: sessionmaker[Session]) -> None:
    """A->B and B->A at the same time is the classic deadlock. Ordered locking prevents it."""
    owner_id, [a, b] = funded_wallets(session_factory, 10_000, 10_000)

    def transfer(index: int) -> str:
        source, destination = (a, b) if index % 2 == 0 else (b, a)
        with session_factory() as session:
            PaymentService(session).transfer(
                user_id=owner_id, source_id=source, destination_id=destination, amount_minor=10
            )
            session.commit()
            return "ok"

    results = run_simultaneously(40, transfer)

    assert results == ["ok"] * 40, [r for r in results if r != "ok"]
    assert balance(session_factory, a) == balance(session_factory, b) == 10_000
    with session_factory() as session:
        assert reconcile(session).ok


def test_concurrent_retries_of_one_request_execute_exactly_once(
    session_factory: sessionmaker[Session],
) -> None:
    owner_id, [wallet_id] = funded_wallets(session_factory, 0)
    with session_factory() as session:
        before = session.scalar(select(func.count()).select_from(LedgerTransaction))

    def submit(_: int) -> IdempotentResult:
        with session_factory() as session:
            payments = PaymentService(session)

            def operation() -> tuple[int, dict[str, object]]:
                transaction = payments.deposit(
                    user_id=owner_id, account_id=wallet_id, amount_minor=500
                )
                return 201, {"id": str(transaction.id)}

            return execute_idempotently(
                session,
                user_id=owner_id,
                key="double-click",
                fingerprint="same-request",
                operation=operation,
            )

    results = run_simultaneously(10, submit)

    assert all(isinstance(r, IdempotentResult) for r in results), results
    outcomes = [r for r in results if isinstance(r, IdempotentResult)]
    assert sum(not r.replayed for r in outcomes) == 1
    assert len({r.body["id"] for r in outcomes}) == 1
    assert balance(session_factory, wallet_id) == 500
    with session_factory() as session:
        after = session.scalar(select(func.count()).select_from(LedgerTransaction))
    assert after == (before or 0) + 1
