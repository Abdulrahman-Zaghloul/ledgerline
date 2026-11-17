"""The database itself refuses to store a corrupt ledger, even if application code has a bug."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from ledger import cli
from ledger.db.models import LedgerTransaction, Posting
from ledger.services.payments import PaymentService
from ledger.services.reconciliation import reconcile
from tests.integration.conftest import create_user, create_wallet, system_account


def _funded(session: Session, amount: int = 1_000) -> tuple[uuid.UUID, uuid.UUID]:
    owner = create_user(session)
    wallet = create_wallet(session, owner)
    transaction = PaymentService(session).deposit(
        user_id=owner.id, account_id=wallet.id, amount_minor=amount
    )
    session.commit()
    return wallet.id, transaction.id


def test_unbalanced_postings_are_rejected_at_commit(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        owner = create_user(session)
        wallet = create_wallet(session, owner)
        session.commit()
        session.add(
            LedgerTransaction(
                id=uuid.uuid4(),
                kind="deposit",
                postings=[Posting(account_id=wallet.id, currency="USD", amount_minor=500)],
            )
        )
        session.flush()  # the insert itself succeeds...
        with pytest.raises(DBAPIError, match="unbalanced"):
            session.commit()  # ...but the deferred trigger refuses to commit it


def test_ledger_rows_are_append_only(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _, transaction_id = _funded(session)
    statements = [
        "UPDATE postings SET amount_minor = amount_minor * 2",
        "DELETE FROM postings",
        "UPDATE ledger_transactions SET description = 'edited'",
        "DELETE FROM ledger_transactions WHERE id = :id",
    ]
    for statement in statements:
        with session_factory() as session, pytest.raises(DBAPIError, match="append-only"):
            session.execute(text(statement), {"id": transaction_id})


def test_user_balances_can_never_go_negative(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        wallet_id, _ = _funded(session)
        with pytest.raises(DBAPIError, match="balance_not_negative"):
            session.execute(
                text("UPDATE accounts SET balance_minor = -1 WHERE id = :id"), {"id": wallet_id}
            )


def test_a_posting_must_match_its_account_currency(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        owner = create_user(session)
        wallet = create_wallet(session, owner, currency="USD")
        funding = system_account(session, "external_funding", "EUR")
        session.add(
            LedgerTransaction(
                id=uuid.uuid4(),
                kind="deposit",
                postings=[
                    Posting(account_id=wallet.id, currency="EUR", amount_minor=100),
                    Posting(account_id=funding.id, currency="EUR", amount_minor=-100),
                ],
            )
        )
        with pytest.raises(DBAPIError, match="fk_postings_account_id_currency_accounts"):
            session.flush()


def test_reconciliation_detects_drift_and_the_cli_fails(
    session_factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    with session_factory() as session:
        wallet_id, _ = _funded(session, 1_000)
    assert cli.main(["reconcile"]) == 0

    with session_factory() as session:  # simulate a bug that edits a cached balance
        session.execute(
            text("UPDATE accounts SET balance_minor = 999 WHERE id = :id"), {"id": wallet_id}
        )
        session.commit()
        report = reconcile(session)

    assert not report.ok
    assert report.drifts[0].cached_minor == 999
    assert report.drifts[0].derived_minor == 1_000
    assert cli.main(["reconcile"]) == 1
    assert "DRIFT" in capsys.readouterr().out


def test_seed_command_is_idempotent(session_factory: sessionmaker[Session]) -> None:
    assert cli.main(["seed"]) == 0
    assert cli.main(["seed"]) == 0
    with session_factory() as session:
        count = session.scalar(text("SELECT count(*) FROM accounts WHERE system_code IS NOT NULL"))
    assert count == 2 * 4  # two system accounts for each of the four default currencies
