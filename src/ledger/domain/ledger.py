"""Double-entry posting rules.

Every movement of money is a *journal entry* made of two or more *lines*. Each line
adds to or subtracts from one account, and the lines of an entry always sum to zero:
money is never created or destroyed, only moved. These functions decide *what* lines
to write; persisting them is someone else's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from ledger.domain.errors import (
    CurrencyMismatch,
    InsufficientFunds,
    InvalidAmount,
    SameAccountTransfer,
    UnbalancedEntry,
)
from ledger.domain.money import Currency, Money


class TransactionKind(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRANSFER = "transfer"


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """What the rules need to know about an account, independent of how it is stored.

    ``balance`` is ``None`` for system accounts, which do not track a cached balance
    and are never checked for sufficient funds (see ADR 0006).
    """

    id: UUID
    currency: Currency
    balance: Money | None


@dataclass(frozen=True, slots=True)
class JournalLine:
    account_id: UUID
    amount: Money


@dataclass(frozen=True, slots=True)
class JournalEntry:
    kind: TransactionKind
    lines: tuple[JournalLine, ...]
    description: str | None = None

    def __post_init__(self) -> None:
        if len(self.lines) < 2:
            raise UnbalancedEntry("A journal entry needs at least two lines")
        currencies = {line.amount.currency for line in self.lines}
        if len(currencies) != 1:
            raise UnbalancedEntry("All lines of a journal entry must share one currency")
        if any(line.amount.is_zero for line in self.lines):
            raise UnbalancedEntry("Journal lines must not be zero")
        if sum(line.amount.minor_units for line in self.lines) != 0:
            raise UnbalancedEntry("Journal lines must sum to zero")

    @property
    def currency(self) -> Currency:
        return self.lines[0].amount.currency

    def net_change(self, account_id: UUID) -> Money:
        total = sum(line.amount.minor_units for line in self.lines if line.account_id == account_id)
        return Money(total, self.currency)


# --- Rules -------------------------------------------------------------------------


def _require_positive(amount: Money) -> None:
    if not amount.is_positive:
        raise InvalidAmount("Amount must be greater than zero")


def _require_currency(account: AccountSnapshot, amount: Money) -> None:
    if account.currency != amount.currency:
        raise CurrencyMismatch(
            f"Account {account.id} holds {account.currency}, not {amount.currency}"
        )


def _require_funds(account: AccountSnapshot, debit: Money) -> None:
    if account.balance is not None and account.balance < debit:
        raise InsufficientFunds(f"Account {account.id} has {account.balance}; {debit} is required")


def _lines(*pairs: tuple[UUID, Money]) -> tuple[JournalLine, ...]:
    return tuple(
        JournalLine(account_id, amount) for account_id, amount in pairs if not amount.is_zero
    )


def calculate_fee(amount: Money, fee_bps: int) -> Money:
    return amount.basis_points(fee_bps)


def plan_deposit(
    *,
    account: AccountSnapshot,
    funding_account: AccountSnapshot,
    amount: Money,
    description: str | None = None,
) -> JournalEntry:
    """Money arrives from outside: the funding account goes down, the wallet goes up."""
    _require_positive(amount)
    _require_currency(account, amount)
    _require_currency(funding_account, amount)
    return JournalEntry(
        TransactionKind.DEPOSIT,
        _lines((funding_account.id, -amount), (account.id, amount)),
        description,
    )


def plan_withdrawal(
    *,
    account: AccountSnapshot,
    funding_account: AccountSnapshot,
    amount: Money,
    description: str | None = None,
) -> JournalEntry:
    """Money leaves to the outside world: the reverse of a deposit."""
    _require_positive(amount)
    _require_currency(account, amount)
    _require_currency(funding_account, amount)
    _require_funds(account, amount)
    return JournalEntry(
        TransactionKind.WITHDRAWAL,
        _lines((account.id, -amount), (funding_account.id, amount)),
        description,
    )


def plan_transfer(
    *,
    source: AccountSnapshot,
    destination: AccountSnapshot,
    amount: Money,
    fee_account: AccountSnapshot,
    fee_bps: int = 0,
    description: str | None = None,
) -> JournalEntry:
    """Move money between two wallets, charging an optional fee to the sender."""
    if source.id == destination.id:
        raise SameAccountTransfer()
    _require_positive(amount)
    _require_currency(source, amount)
    _require_currency(destination, amount)
    _require_currency(fee_account, amount)
    fee = calculate_fee(amount, fee_bps)
    _require_funds(source, amount + fee)
    return JournalEntry(
        TransactionKind.TRANSFER,
        _lines(
            (source.id, -(amount + fee)),
            (destination.id, amount),
            (fee_account.id, fee),
        ),
        description,
    )
