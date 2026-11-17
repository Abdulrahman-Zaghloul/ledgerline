from decimal import Decimal

import pytest

from ledger.domain.errors import CurrencyMismatch, InvalidAmount, UnsupportedCurrency
from ledger.domain.money import Money, get_currency


def test_floats_cannot_represent_money_but_integers_can() -> None:
    assert 0.1 + 0.2 != 0.3  # the reason this module exists
    assert Money.from_decimal("0.10", "USD") + Money.from_decimal("0.20", "USD") == Money.of(
        30, "USD"
    )


def test_money_refuses_to_be_built_from_a_float() -> None:
    with pytest.raises(TypeError):
        Money(10.5, get_currency("USD"))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money.from_decimal(10.5, "USD")  # type: ignore[arg-type]


def test_money_refuses_booleans() -> None:
    with pytest.raises(TypeError):
        Money(True, get_currency("USD"))


@pytest.mark.parametrize(
    ("text", "currency", "minor_units"),
    [("12.34", "USD", 1234), ("0.01", "EUR", 1), ("1500", "JPY", 1500), ("1.234", "KWD", 1234)],
)
def test_from_decimal_converts_to_minor_units(text: str, currency: str, minor_units: int) -> None:
    assert Money.from_decimal(text, currency).minor_units == minor_units


@pytest.mark.parametrize(("text", "currency"), [("12.345", "USD"), ("10.5", "JPY")])
def test_from_decimal_rejects_more_precision_than_the_currency_has(
    text: str, currency: str
) -> None:
    with pytest.raises(InvalidAmount):
        Money.from_decimal(text, currency)


@pytest.mark.parametrize("text", ["abc", "NaN", "Infinity", ""])
def test_from_decimal_rejects_non_numbers(text: str) -> None:
    with pytest.raises(InvalidAmount):
        Money.from_decimal(text, "USD")


def test_unknown_currency_is_rejected() -> None:
    with pytest.raises(UnsupportedCurrency):
        get_currency("XYZ")


def test_currency_codes_are_normalised() -> None:
    assert get_currency(" usd ").code == "USD"


def test_arithmetic_and_comparison_within_one_currency() -> None:
    ten, three = Money.of(1000, "USD"), Money.of(300, "USD")
    assert ten - three == Money.of(700, "USD")
    assert -three == Money.of(-300, "USD")
    assert three < ten
    assert ten > three
    assert three <= Money.of(300, "USD")
    assert ten >= three


@pytest.mark.parametrize(
    "operation",
    [
        lambda a, b: a + b,
        lambda a, b: a - b,
        lambda a, b: a < b,
        lambda a, b: a <= b,
        lambda a, b: a > b,
        lambda a, b: a >= b,
    ],
)
def test_mixing_currencies_is_an_error(operation: object) -> None:
    with pytest.raises(CurrencyMismatch):
        operation(Money.of(100, "USD"), Money.of(100, "EUR"))  # type: ignore[operator]


@pytest.mark.parametrize(
    ("amount", "bps", "expected"),
    [
        (2500, 50, 12),  # 12.5 -> 12: exact half rounds to the even neighbour
        (2700, 50, 14),  # 13.5 -> 14
        (2520, 50, 13),  # 12.6 -> 13
        (1, 5000, 0),  # 0.5 -> 0
        (3, 5000, 2),  # 1.5 -> 2
        (300, 5000, 150),
        (100, 25, 0),
        (-2500, 50, -12),
        (2500, 0, 0),
    ],
)
def test_basis_points_use_bankers_rounding(amount: int, bps: int, expected: int) -> None:
    assert Money.of(amount, "USD").basis_points(bps) == Money.of(expected, "USD")


def test_negative_basis_points_are_rejected() -> None:
    with pytest.raises(InvalidAmount):
        Money.of(100, "USD").basis_points(-1)


@pytest.mark.parametrize(
    ("minor_units", "currency", "text"),
    [(1050, "USD", "10.50 USD"), (1500, "JPY", "1500 JPY"), (-5, "USD", "-0.05 USD")],
)
def test_string_form(minor_units: int, currency: str, text: str) -> None:
    assert str(Money.of(minor_units, currency)) == text


def test_to_decimal_is_exact() -> None:
    assert Money.of(1999, "USD").to_decimal() == Decimal("19.99")
