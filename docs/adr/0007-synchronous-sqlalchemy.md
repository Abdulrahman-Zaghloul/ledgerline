# 7. Synchronous SQLAlchemy over async

**Status:** Accepted

## Context

FastAPI supports both `async def` endpoints with an async database driver and plain
`def` endpoints, which it runs in a thread pool. Async shines when a request waits on many
independent I/O calls at once. Every request here is a short database transaction that
needs row locks and a strict order of operations.

## Decision

Use synchronous SQLAlchemy 2.0 with `psycopg` and plain `def` endpoints.

## Consequences

- Transaction and locking code reads top to bottom, with no `await` points where a
  coroutine could be suspended while holding a lock.
- Concurrency tests use ordinary threads and sessions.
- Concurrency per process is bounded by the thread pool and the connection pool, which
  are sized together (`LEDGER_DB_POOL_SIZE`). Scaling is horizontal: more API replicas.
- Revisit if endpoints start fanning out to several slow external services per request.
