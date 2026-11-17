# 2. Money as integer minor units

**Status:** Accepted

## Context

Binary floating point cannot represent most decimal fractions: in Python,
`0.1 + 0.2 == 0.30000000000000004`. Errors accumulate across millions of operations and
differ between languages and hardware. `Decimal` is exact but easy to misuse (precision
contexts, mixing with floats) and awkward to send through JSON.

## Decision

- Store and compute every amount as an **integer number of minor units** (cents for USD,
  yen for JPY, fils for KWD) in a `BIGINT` column.
- Wrap amounts in an immutable `Money(minor_units, currency)` value object that refuses
  floats, refuses arithmetic across currencies, and knows each currency's ISO 4217 exponent.
- The API accepts and returns integer minor units (as Stripe does) and validates them as
  strict integers, so `10.5` and `"100"` are rejected rather than coerced.
- Percentage fees use exact integer arithmetic with **round-half-to-even** (banker's
  rounding), which avoids the upward bias of always rounding halves up.

## Consequences

- Arithmetic is exact and identical on every machine.
- Clients must convert for display. `balance_display` is provided for convenience.
- `BIGINT` caps one value at about 9.2 × 10¹⁸ minor units; amounts are additionally
  capped at 10¹² per request.
