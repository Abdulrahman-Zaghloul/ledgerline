from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ledger.db.models import Account
from ledger.domain.errors import LedgerError

SYSTEM_ACCOUNTS: dict[str, str] = {
    "external_funding": "External funding (money entering or leaving the platform)",
    "fees": "Platform fee revenue",
}


class AccountRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, account: Account) -> Account:
        self._session.add(account)
        self._session.flush()
        return account

    def get(self, account_id: uuid.UUID) -> Account | None:
        return self._session.get(Account, account_id)

    def list_for_owner(self, owner_id: uuid.UUID) -> list[Account]:
        stmt = select(Account).where(Account.owner_id == owner_id).order_by(Account.created_at)
        return list(self._session.scalars(stmt))

    def lock_user_accounts(self, account_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Account]:
        """Lock user wallets with SELECT ... FOR UPDATE, always in ascending id order.

        Every code path acquires locks in the same global order, so two transfers
        touching the same pair of accounts can never wait on each other in a cycle
        (no deadlocks). System accounts are never locked, even if a caller passes
        their id (ADR 0006). ``populate_existing`` refreshes any copy already in the
        session so balance checks see the latest committed value.
        """
        ids = sorted(set(account_ids))
        stmt = (
            select(Account)
            .where(Account.id.in_(ids), Account.owner_id.is_not(None))
            .order_by(Account.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return {account.id: account for account in self._session.scalars(stmt)}

    def get_system_account(self, code: str, currency: str) -> Account:
        stmt = select(Account).where(Account.system_code == code, Account.currency == currency)
        account = self._session.scalars(stmt).one_or_none()
        if account is None:
            raise LedgerError(f"System account {code}/{currency} is missing; run seeding")
        return account

    def ensure_system_accounts(self, currencies: Iterable[str]) -> None:
        """Idempotently create the platform's system accounts for each currency."""
        rows = [
            {"id": uuid.uuid4(), "system_code": code, "name": name, "currency": currency}
            for currency in currencies
            for code, name in SYSTEM_ACCOUNTS.items()
        ]
        if not rows:
            return
        stmt = (
            insert(Account)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["system_code", "currency"],
                index_where=Account.system_code.is_not(None),
            )
        )
        self._session.execute(stmt)
