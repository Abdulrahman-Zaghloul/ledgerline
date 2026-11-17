"""Opening wallets and reading balances, statements and transactions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ledger.db.models import Account, LedgerTransaction, Posting
from ledger.domain.errors import AccountNotFound, TransactionNotFound, UnsupportedCurrency
from ledger.domain.money import get_currency
from ledger.repositories.accounts import AccountRepository
from ledger.repositories.transactions import TransactionRepository
from ledger.services.pagination import decode_cursor, encode_cursor


@dataclass(frozen=True, slots=True)
class StatementPage:
    lines: list[tuple[Posting, LedgerTransaction]]
    next_cursor: str | None


class AccountService:
    def __init__(self, session: Session, supported_currencies: tuple[str, ...]) -> None:
        self._accounts = AccountRepository(session)
        self._transactions = TransactionRepository(session)
        self._supported = supported_currencies

    def open_account(self, *, owner_id: uuid.UUID, name: str, currency: str) -> Account:
        code = get_currency(currency).code
        if code not in self._supported:
            raise UnsupportedCurrency(f"Currency {code} is not enabled on this platform")
        return self._accounts.add(
            Account(id=uuid.uuid4(), owner_id=owner_id, name=name, currency=code, balance_minor=0)
        )

    def list_accounts(self, owner_id: uuid.UUID) -> list[Account]:
        return self._accounts.list_for_owner(owner_id)

    def get_owned_account(self, *, owner_id: uuid.UUID, account_id: uuid.UUID) -> Account:
        """Someone else's account is reported as *not found*, never as *forbidden*:
        a 403 would confirm to an attacker that the id exists."""
        account = self._accounts.get(account_id)
        if account is None or account.owner_id != owner_id:
            raise AccountNotFound()
        return account

    def statement(
        self, *, owner_id: uuid.UUID, account_id: uuid.UUID, limit: int, cursor: str | None
    ) -> StatementPage:
        self.get_owned_account(owner_id=owner_id, account_id=account_id)
        before = decode_cursor(cursor) if cursor else None
        # Fetch one extra row to learn whether another page exists without a COUNT query.
        rows = self._transactions.statement_page(
            account_id, limit=limit + 1, before_posting_id=before
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1][0].id) if has_more else None
        return StatementPage(lines=rows, next_cursor=next_cursor)

    def get_transaction(
        self, *, owner_id: uuid.UUID, transaction_id: uuid.UUID
    ) -> LedgerTransaction:
        transaction = self._transactions.get_visible_to(transaction_id, owner_id)
        if transaction is None:
            raise TransactionNotFound()
        return transaction
