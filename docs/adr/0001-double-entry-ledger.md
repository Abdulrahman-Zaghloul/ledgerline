# 1. Double-entry ledger with append-only postings

**Status:** Accepted

## Context

The simplest wallet design is a `balance` column that is incremented and decremented.
It has no audit trail, cannot explain how a balance was reached, and a single bug can
create or destroy money with no trace. Payment systems need to answer "where did this
money come from and where did it go?" for every cent, forever.

## Decision

Record every movement as a **ledger transaction** made of two or more **postings**
(signed amounts against accounts) that sum to zero per currency. Money entering or
leaving the platform moves through **system accounts** (`external_funding`, `fees`), so
even deposits and withdrawals balance.

The rule is enforced in three places, from friendliest to strictest:

1. The domain (`JournalEntry.__post_init__`) refuses to construct an unbalanced entry.
2. A `DEFERRABLE INITIALLY DEFERRED` constraint trigger re-checks the sum at `COMMIT`,
   after all postings of the transaction exist.
3. `BEFORE UPDATE OR DELETE` triggers make `postings` and `ledger_transactions`
   append-only. Mistakes are corrected with new, compensating transactions.

## Consequences

- Every balance is explainable and independently verifiable (`ledgerline reconcile`).
- A bug in application code cannot commit an unbalanced or edited ledger.
- Writes cost more: one movement inserts 3–4 rows instead of updating one.
- Test cleanup must use `TRUNCATE`, which bypasses row-level triggers.

## Alternatives considered

- **Balance column only:** simplest, but unauditable. Rejected.
- **Event sourcing with no cached balances:** elegant, but every balance read becomes a
  sum over history. We keep a cached balance on user accounts and verify it instead.
