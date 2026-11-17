from __future__ import annotations

import uuid

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from ledger.db.models import Account, LedgerTransaction, Posting


class TransactionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, transaction: LedgerTransaction) -> LedgerTransaction:
        self._session.add(transaction)
        self._session.flush()
        return transaction

    def get_visible_to(
        self, transaction_id: uuid.UUID, user_id: uuid.UUID
    ) -> LedgerTransaction | None:
        """A user may see a transaction if it touched at least one of their accounts."""
        touches_user = exists().where(
            Posting.transaction_id == LedgerTransaction.id,
            Posting.account_id == Account.id,
            Account.owner_id == user_id,
        )
        stmt = select(LedgerTransaction).where(LedgerTransaction.id == transaction_id, touches_user)
        return self._session.scalars(stmt).one_or_none()

    def statement_page(
        self, account_id: uuid.UUID, *, limit: int, before_posting_id: int | None
    ) -> list[tuple[Posting, LedgerTransaction]]:
        """Newest-first postings for one account, using keyset pagination.

        ``WHERE id < :cursor ORDER BY id DESC LIMIT n`` stays fast on page 10,000,
        unlike ``OFFSET``, which reads and discards every earlier row.
        """
        stmt = (
            select(Posting, LedgerTransaction)
            .join(LedgerTransaction, Posting.transaction_id == LedgerTransaction.id)
            .where(Posting.account_id == account_id)
            .order_by(Posting.id.desc())
            .limit(limit)
        )
        if before_posting_id is not None:
            stmt = stmt.where(Posting.id < before_posting_id)
        return [(row[0], row[1]) for row in self._session.execute(stmt)]
