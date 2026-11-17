"""Money as a value object: an integer count of minor units plus a currency.

Floats cannot represent most decimal fractions exactly (0.1 + 0.2 != 0.3), so money
is never stored or computed as a float anywhere in this codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ledger.domain.errors import CurrencyMismatch, InvalidAmount, UnsupportedCurrency

BASIS_POINTS_PER_UNIT = 10_000

# ISO 4217 minor-unit exponents: how many digits follow the decimal point.
_ISO_4217_EXPONENTS: dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CAD": 2,
    "AUD": 2,
    "CHF": 2,
    "JPY": 0,
    "KWD": 3,
}


@dataclass(frozen=True, slots=True)
class Currency:
    code: str
    exponent: int

    def __str__(self) -> str:
        return self.code


def get_currency(code: str) -> Currency:
    normalised = code.strip().upper()
    try:
        return Currency(normalised, _ISO_4217_EXPONENTS[normalised])
    except KeyError:
        raise UnsupportedCurrency(f"Currency {code!r} is not supported") from None


@dataclass(frozen=True, slots=True)
class Money:
    minor_units: int
    currency: Currency

    def __post_init__(self) -> None:
        if isinstance(self.minor_units, bool) or not isinstance(self.minor_units, int):
            raise TypeError("Money takes an integer number of minor units, never a float")

    # --- Construction ---------------------------------------------------------------

    @classmethod
    def of(cls, minor_units: int, currency_code: str) -> Money:
        return cls(minor_units, get_currency(currency_code))

    @classmethod
    def zero(cls, currency_code: str) -> Money:
        return cls.of(0, currency_code)

    @classmethod
    def from_decimal(cls, value: Decimal | str, currency_code: str) -> Money:
        """Parse a human amount such as "12.34". Rejects sub-minor-unit precision."""
        if isinstance(value, float):
            raise TypeError("Refusing to build Money from a float; pass a str or Decimal")
        currency = get_currency(currency_code)
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise InvalidAmount(f"{value!r} is not a number") from None
        if not amount.is_finite():
            raise InvalidAmount(f"{value!r} is not a finite amount")
        scaled = amount.scaleb(currency.exponent)
        if scaled != scaled.to_integral_value():
            raise InvalidAmount(
                f"{currency.code} supports at most {currency.exponent} decimal places"
            )
        return cls(int(scaled), currency)

    # --- Arithmetic -----------------------------------------------------------------

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(f"Cannot combine {self.currency} with {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.minor_units - other.minor_units, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.minor_units, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.minor_units < other.minor_units

    def __le__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.minor_units <= other.minor_units

    def __gt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.minor_units > other.minor_units

    def __ge__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.minor_units >= other.minor_units

    def basis_points(self, bps: int) -> Money:
        """Return bps/10,000 of this amount, rounded half-to-even (banker's rounding).

        Pure integer arithmetic, so the result is exact and identical on every machine.
        """
        if bps < 0:
            raise InvalidAmount("Basis points cannot be negative")
        sign = -1 if self.minor_units < 0 else 1
        quotient, remainder = divmod(abs(self.minor_units) * bps, BASIS_POINTS_PER_UNIT)
        twice_remainder = remainder * 2
        if twice_remainder > BASIS_POINTS_PER_UNIT or (
            twice_remainder == BASIS_POINTS_PER_UNIT and quotient % 2 == 1
        ):
            quotient += 1
        return Money(sign * quotient, self.currency)

    # --- Queries --------------------------------------------------------------------

    @property
    def is_positive(self) -> bool:
        return self.minor_units > 0

    @property
    def is_zero(self) -> bool:
        return self.minor_units == 0

    def to_decimal(self) -> Decimal:
        return Decimal(self.minor_units).scaleb(-self.currency.exponent)

    def __str__(self) -> str:
        return f"{self.to_decimal():.{self.currency.exponent}f} {self.currency.code}"
