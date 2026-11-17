"""Deposits, withdrawals and transfers.

Each operation follows the same four steps inside the caller's database transaction:

1. Lock the user accounts involved (ascending id order, so no deadlocks).
2. Ask the domain rules to plan a balanced journal entry (or refuse).
3. Write the ledger transaction, its postings and the new cached balances.
4. Write an outbox event describing what happened.

Nothing is committed here; the caller commits all four steps atomically.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ledger.db.models import Account, LedgerTransaction, Posting
from ledger.domain.errors import AccountNotFound
from ledger.domain.ledger import (
    AccountSnapshot,
    JournalEntry,
    plan_deposit,
    plan_transfer,
    plan_withdrawal,
)
from ledger.domain.money import Money, get_currency
from ledger.repositories.accounts import AccountRepository
from ledger.repositories.outbox import OutboxRepository
from ledger.repositories.transactions import TransactionRepository

FUNDING_ACCOUNT = "external_funding"
FEE_ACCOUNT = "fees"
TRANSACTION_RECORDED = "ledger.transaction.recorded"


def snapshot(account: Account) -> AccountSnapshot:
    currency = get_currency(account.currency)
    balance = None if account.balance_minor is None else Money(account.balance_minor, currency)
    return AccountSnapshot(id=account.id, currency=currency, balance=balance)


def transaction_event_payload(transaction: LedgerTransaction) -> dict[str, Any]:
    return {
        "transaction_id": str(transaction.id),
        "kind": transaction.kind,
        "description": transaction.description,
        "created_at": transaction.created_at.isoformat(),
        "postings": [
            {"account_id": str(p.account_id), "amount": p.amount_minor, "currency": p.currency}
            for p in transaction.postings
        ],
    }


class PaymentService:
    def __init__(self, session: Session, *, fee_bps: int = 0) -> None:
        self._accounts = AccountRepository(session)
        self._transactions = TransactionRepository(session)
        self._outbox = OutboxRepository(session)
        self._fee_bps = fee_bps

    def deposit(
        self,
        *,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        amount_minor: int,
        description: str | None = None,
    ) -> LedgerTransaction:
        locked = self._accounts.lock_user_accounts([account_id])
        account = self._owned(locked, account_id, user_id)
        funding = self._accounts.get_system_account(FUNDING_ACCOUNT, account.currency)
        entry = plan_deposit(
            account=snapshot(account),
            funding_account=snapshot(funding),
            amount=Money.of(amount_minor, account.currency),
            description=description,
        )
        return self._record(entry, user_id, locked)

    def withdraw(
        self,
        *,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        amount_minor: int,
        description: str | None = None,
    ) -> LedgerTransaction:
        locked = self._accounts.lock_user_accounts([account_id])
        account = self._owned(locked, account_id, user_id)
        funding = self._accounts.get_system_account(FUNDING_ACCOUNT, account.currency)
        entry = plan_withdrawal(
            account=snapshot(account),
            funding_account=snapshot(funding),
            amount=Money.of(amount_minor, account.currency),
            description=description,
        )
        return self._record(entry, user_id, locked)

    def transfer(
        self,
        *,
        user_id: uuid.UUID,
        source_id: uuid.UUID,
        destination_id: uuid.UUID,
        amount_minor: int,
        description: str | None = None,
    ) -> LedgerTransaction:
        locked = self._accounts.lock_user_accounts([source_id, destination_id])
        source = self._owned(locked, source_id, user_id)
        destination = locked.get(destination_id)
        if destination is None:  # unknown, or a system account (never locked, never a target)
            raise AccountNotFound(f"Destination account {destination_id} not found")
        fees = self._accounts.get_system_account(FEE_ACCOUNT, source.currency)
        entry = plan_transfer(
            source=snapshot(source),
            destination=snapshot(destination),
            amount=Money.of(amount_minor, source.currency),
            fee_account=snapshot(fees),
            fee_bps=self._fee_bps,
            description=description,
        )
        return self._record(entry, user_id, locked)

    @staticmethod
    def _owned(
        locked: dict[uuid.UUID, Account], account_id: uuid.UUID, user_id: uuid.UUID
    ) -> Account:
        account = locked.get(account_id)
        if account is None or account.owner_id != user_id:
            raise AccountNotFound(f"Account {account_id} not found")
        return account

    def _record(
        self, entry: JournalEntry, user_id: uuid.UUID, locked: dict[uuid.UUID, Account]
    ) -> LedgerTransaction:
        transaction = LedgerTransaction(
            id=uuid.uuid4(),
            kind=entry.kind.value,
            description=entry.description,
            initiated_by=user_id,
            postings=[
                Posting(
                    account_id=line.account_id,
                    currency=line.amount.currency.code,
                    amount_minor=line.amount.minor_units,
                )
                for line in entry.lines
            ],
        )
        self._transactions.add(transaction)
        # Only locked user accounts carry a cached balance; system accounts are derived.
        for account in locked.values():
            if account.balance_minor is not None:
                account.balance_minor += entry.net_change(account.id).minor_units
        self._outbox.add(
            event_type=TRANSACTION_RECORDED,
            aggregate_id=transaction.id,
            payload=transaction_event_payload(transaction),
        )
        return transaction
