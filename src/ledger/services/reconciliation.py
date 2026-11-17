"""Independent verification that the books are correct.

Two invariants must always hold:

1. Every user account's cached balance equals the sum of its postings.
2. Across the whole ledger, postings in each currency sum to exactly zero.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledger.db.models import Account, Posting


@dataclass(frozen=True, slots=True)
class BalanceDrift:
    account_id: uuid.UUID
    cached_minor: int
    derived_minor: int


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    accounts_checked: int
    drifts: list[BalanceDrift] = field(default_factory=list)
    currency_imbalances: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.drifts and not self.currency_imbalances


def reconcile(session: Session) -> ReconciliationReport:
    derived = func.coalesce(func.sum(Posting.amount_minor), 0)
    per_account = (
        select(Account.id, Account.balance_minor, derived.label("derived"))
        .outerjoin(Posting, Posting.account_id == Account.id)
        .where(Account.balance_minor.is_not(None))
        .group_by(Account.id, Account.balance_minor)
    )
    rows = session.execute(per_account).all()
    drifts = [
        BalanceDrift(account_id=row.id, cached_minor=row.balance_minor, derived_minor=row.derived)
        for row in rows
        if row.balance_minor != row.derived
    ]

    per_currency = (
        select(Posting.currency, func.sum(Posting.amount_minor))
        .group_by(Posting.currency)
        .having(func.sum(Posting.amount_minor) != 0)
    )
    imbalances = {currency: int(total) for currency, total in session.execute(per_currency)}
    return ReconciliationReport(
        accounts_checked=len(rows), drifts=drifts, currency_imbalances=imbalances
    )
