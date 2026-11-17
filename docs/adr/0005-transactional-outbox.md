# 5. Transactional outbox for events

**Status:** Accepted

## Context

Other systems (notifications, analytics, fraud checks) need to know when money moves.
Publishing directly from the request handler creates a *dual write*. If we commit and
then the publish fails, the event is lost. If we publish and then the commit fails, we
announced something that never happened.

## Decision

Write an `outbox_events` row **in the same transaction** as the ledger change. A
separate relay process (`ledgerline relay`):

- claims due events with `SELECT … FOR UPDATE SKIP LOCKED`, so several relays can run in
  parallel without delivering the same event twice concurrently;
- publishes through a `Publisher` protocol (logging by default, or HMAC-signed webhooks);
- on failure, retries with exponential backoff (2s, 4s, 8s … capped at 5 min) plus up to
  10% jitter, and dead-letters an event after a configurable number of attempts.

## Consequences

- An event exists if and only if its transaction committed.
- Delivery is **at least once**. A crash after a successful webhook but before marking
  the event published causes a redelivery, so each event carries a stable id that
  receivers use to de-duplicate.
- Events are delayed by up to one poll interval (1 s by default).
- The relay holds row locks while calling a webhook. That is acceptable at this scale; a
  high-volume version would claim events, release the locks, then publish.
