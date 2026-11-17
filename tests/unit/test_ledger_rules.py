import uuid

import pytest

from ledger.domain.errors import (
    CurrencyMismatch,
    InsufficientFunds,
    InvalidAmount,
    SameAccountTransfer,
    UnbalancedEntry,
)
from ledger.domain.ledger import (
    AccountSnapshot,
    JournalEntry,
    JournalLine,
    TransactionKind,
    plan_deposit,
    plan_transfer,
    plan_withdrawal,
)
from ledger.domain.money import Money, get_currency


def wallet(balance: int, currency: str = "USD") -> AccountSnapshot:
    return AccountSnapshot(uuid.uuid4(), get_currency(currency), Money.of(balance, currency))


def system(currency: str = "USD") -> AccountSnapshot:
    return AccountSnapshot(uuid.uuid4(), get_currency(currency), balance=None)


def usd(minor_units: int) -> Money:
    return Money.of(minor_units, "USD")


def test_deposit_moves_money_from_the_funding_account_into_the_wallet() -> None:
    account, funding = wallet(0), system()
    entry = plan_deposit(account=account, funding_account=funding, amount=usd(500))
    assert entry.kind is TransactionKind.DEPOSIT
    assert entry.net_change(account.id) == usd(500)
    assert entry.net_change(funding.id) == usd(-500)


def test_withdrawal_requires_sufficient_funds() -> None:
    with pytest.raises(InsufficientFunds):
        plan_withdrawal(account=wallet(499), funding_account=system(), amount=usd(500))


def test_withdrawal_of_the_exact_balance_is_allowed() -> None:
    account = wallet(500)
    entry = plan_withdrawal(account=account, funding_account=system(), amount=usd(500))
    assert entry.net_change(account.id) == usd(-500)


def test_transfer_without_fee_has_two_lines() -> None:
    source, destination = wallet(1000), wallet(0)
    entry = plan_transfer(
        source=source, destination=destination, amount=usd(400), fee_account=system()
    )
    assert len(entry.lines) == 2  # a zero fee line is omitted, not written as 0
    assert entry.net_change(source.id) == usd(-400)
    assert entry.net_change(destination.id) == usd(400)


def test_transfer_fee_is_charged_to_the_sender_and_credited_to_the_fee_account() -> None:
    source, destination, fees = wallet(10_000), wallet(0), system()
    entry = plan_transfer(
        source=source, destination=destination, amount=usd(2500), fee_account=fees, fee_bps=50
    )
    assert entry.net_change(source.id) == usd(-2512)
    assert entry.net_change(destination.id) == usd(2500)
    assert entry.net_change(fees.id) == usd(12)
    assert sum(line.amount.minor_units for line in entry.lines) == 0


def test_transfer_requires_funds_for_amount_plus_fee() -> None:
    with pytest.raises(InsufficientFunds):
        plan_transfer(
            source=wallet(1000),
            destination=wallet(0),
            amount=usd(1000),
            fee_account=system(),
            fee_bps=100,
        )


def test_transfer_to_the_same_account_is_refused() -> None:
    account = wallet(1000)
    with pytest.raises(SameAccountTransfer):
        plan_transfer(source=account, destination=account, amount=usd(1), fee_account=system())


def test_transfer_between_currencies_is_refused() -> None:
    with pytest.raises(CurrencyMismatch):
        plan_transfer(
            source=wallet(1000), destination=wallet(0, "EUR"), amount=usd(1), fee_account=system()
        )


@pytest.mark.parametrize("amount", [0, -1])
def test_amounts_must_be_positive(amount: int) -> None:
    with pytest.raises(InvalidAmount):
        plan_deposit(account=wallet(0), funding_account=system(), amount=usd(amount))


def test_system_accounts_are_never_checked_for_funds() -> None:
    # The funding account has no tracked balance, so it can pay out any deposit.
    plan_deposit(account=wallet(0), funding_account=system(), amount=usd(10**12))


def test_journal_entries_must_balance() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(UnbalancedEntry, match="sum to zero"):
        JournalEntry(TransactionKind.DEPOSIT, (JournalLine(a, usd(100)), JournalLine(b, usd(-99))))


def test_journal_entries_need_two_lines() -> None:
    with pytest.raises(UnbalancedEntry, match="at least two"):
        JournalEntry(TransactionKind.DEPOSIT, (JournalLine(uuid.uuid4(), usd(0)),))


def test_journal_entries_cannot_mix_currencies() -> None:
    lines = (
        JournalLine(uuid.uuid4(), usd(100)),
        JournalLine(uuid.uuid4(), Money.of(-100, "EUR")),
    )
    with pytest.raises(UnbalancedEntry, match="one currency"):
        JournalEntry(TransactionKind.TRANSFER, lines)


def test_journal_lines_cannot_be_zero() -> None:
    lines = (
        JournalLine(uuid.uuid4(), usd(0)),
        JournalLine(uuid.uuid4(), usd(0)),
    )
    with pytest.raises(UnbalancedEntry, match="not be zero"):
        JournalEntry(TransactionKind.TRANSFER, lines)
