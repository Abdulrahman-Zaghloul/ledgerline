# 6. System accounts without cached balances

**Status:** Accepted

## Context

Every deposit and withdrawal posts to the `external_funding` account for its currency,
and every fee posts to `fees`. If those accounts kept a cached balance, every money
movement would `SELECT … FOR UPDATE` the same row. All deposits on the platform would
then queue behind one lock: a classic **hot row**.

## Decision

Only user accounts keep a cached `balance_minor`. System accounts store `NULL`, are never
locked, and their balance is derived from postings when needed. The schema enforces this
with `CHECK ((owner_id IS NULL) = (balance_minor IS NULL))`.

Inserting a posting that references a system account takes only a `FOR KEY SHARE` lock
(from the foreign key check), and those locks do not conflict with each other.

## Consequences

- Deposits, withdrawals and fees on different user accounts proceed fully in parallel.
- System accounts are never checked for sufficient funds, which is correct: the external
  funding account represents the outside world and is expected to go negative.
- Reading a system account's balance means summing its postings. That is fine for
  reporting and reconciliation, which are not on the request path.
