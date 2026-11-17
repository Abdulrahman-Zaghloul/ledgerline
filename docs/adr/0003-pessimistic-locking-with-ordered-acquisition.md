# 3. Pessimistic row locks with ordered acquisition

**Status:** Accepted

## Context

Two concurrent withdrawals can both read a balance of 1000, both decide 600 is
affordable, and both commit. That is a *lost update*, and it lets a user spend money
twice. PostgreSQL's default `READ COMMITTED` isolation does not prevent it.

## Decision

Inside the database transaction, lock every user account involved with
`SELECT … FOR UPDATE` **before** reading balances, and always lock in **ascending
account id order** (`AccountRepository.lock_user_accounts`).

- The lock makes concurrent movements on the same account take turns.
- A single global lock order makes deadlocks impossible: two transfers A→B and B→A both
  lock A first, so neither can hold one lock while waiting for the other.
- `populate_existing` refreshes any copy of the row already in the session, so the funds
  check always sees the latest committed balance.
- A `CHECK (balance_minor >= 0)` constraint is the backstop if a code path ever forgets.

## Consequences

- Correct under any concurrency, which the concurrency tests prove: 20 parallel
  withdrawals against a balance for 10 yield exactly 10 successes. Removing the lock makes
  that test fail.
- Throughput on a *single* account is bounded by lock hold time. This matters only for
  very hot accounts (see ADR 0006 and the README's next steps).

## Alternatives considered

- **Optimistic locking (version column, retry on conflict):** good when conflicts are
  rare, but payments to popular accounts conflict often, and retries add latency and
  complexity at the edges.
- **`SERIALIZABLE` isolation:** correct, but every conflict surfaces as a serialization
  failure that callers must retry. Explicit locks state the intent more clearly.
- **Atomic `UPDATE … SET balance = balance - x WHERE balance >= x`:** good for one
  account, but a transfer needs a consistent view of several rows plus postings.
