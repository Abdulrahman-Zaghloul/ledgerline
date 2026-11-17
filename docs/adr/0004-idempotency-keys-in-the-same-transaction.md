# 4. Idempotency keys committed with the operation

**Status:** Accepted

## Context

Networks fail after the server has done the work but before the client hears back. The
client cannot tell "never happened" from "happened, reply lost", so it retries. Without
protection, a retried transfer moves money twice.

## Decision

Every money-moving endpoint requires an `Idempotency-Key` header, scoped per user, and
`execute_idempotently` runs this sequence **in one database transaction**:

1. `INSERT` the key with a SHA-256 fingerprint of method, path and body
   (`ON CONFLICT DO NOTHING`). If a concurrent request holds the same key uncommitted,
   PostgreSQL makes this insert **wait** for it, which serialises duplicate requests
   without an "in progress" state.
2. Key already present with the same fingerprint → return the stored response with
   `Idempotent-Replayed: true`. Different fingerprint → `422 idempotency-key-reused`.
3. Otherwise run the operation, store its response on the key row, and commit everything.

If the operation fails (for example, insufficient funds), everything rolls back
**including the key**, so the client may fix the cause and retry with the same key.
Keys are purged after 24 hours by the relay worker.

## Consequences

- No window exists in which money moved but the response was lost, or the reverse.
- Concurrent duplicates are tested: 10 simultaneous submissions produce exactly one transaction.
- This works because the operation's only side effects are in the **same database**. An
  operation that called an external system (a card network) would need the multi-phase
  "recovery point" design, where the key moves through persisted states and can resume
  after a crash.
- Failed attempts are not cached. That differs from Stripe, which also caches errors. We
  chose "a failure changed nothing, so retry freely" because it is simpler for clients.
