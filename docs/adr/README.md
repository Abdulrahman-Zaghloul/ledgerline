# Architecture Decision Records

Short documents capturing *why* a significant decision was made, in the format proposed by
Michael Nygard: context, decision, consequences. They are never rewritten; a reversed
decision gets a new record that supersedes the old one.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-double-entry-ledger.md) | Double-entry ledger with append-only postings | Accepted |
| [0002](0002-money-as-integer-minor-units.md) | Money as integer minor units | Accepted |
| [0003](0003-pessimistic-locking-with-ordered-acquisition.md) | Pessimistic row locks with ordered acquisition | Accepted |
| [0004](0004-idempotency-keys-in-the-same-transaction.md) | Idempotency keys committed with the operation | Accepted |
| [0005](0005-transactional-outbox.md) | Transactional outbox for events | Accepted |
| [0006](0006-system-accounts-without-cached-balances.md) | System accounts without cached balances | Accepted |
| [0007](0007-synchronous-sqlalchemy.md) | Synchronous SQLAlchemy over async | Accepted |
