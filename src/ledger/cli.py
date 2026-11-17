"""Operator commands: ``ledgerline seed | reconcile | relay``."""

from __future__ import annotations

import argparse
import sys

from ledger.config import Settings
from ledger.db.session import build_engine, build_session_factory
from ledger.repositories.accounts import AccountRepository
from ledger.services.reconciliation import reconcile
from ledger.workers import outbox_relay


def _seed(settings: Settings) -> int:
    engine = build_engine(settings)
    with build_session_factory(engine)() as session:
        AccountRepository(session).ensure_system_accounts(settings.supported_currencies)
        session.commit()
    engine.dispose()
    print(f"System accounts ensured for: {', '.join(settings.supported_currencies)}")
    return 0


def _reconcile(settings: Settings) -> int:
    engine = build_engine(settings)
    with build_session_factory(engine)() as session:
        report = reconcile(session)
    engine.dispose()
    print(f"Accounts checked: {report.accounts_checked}")
    for drift in report.drifts:
        print(
            f"  DRIFT {drift.account_id}: cached={drift.cached_minor} derived={drift.derived_minor}"
        )
    for currency, total in report.currency_imbalances.items():
        print(f"  IMBALANCE {currency}: postings sum to {total}, expected 0")
    print("Books balance." if report.ok else "Reconciliation FAILED.")
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledgerline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="Create system accounts for every supported currency")
    sub.add_parser("reconcile", help="Verify balances against postings; exit 1 on mismatch")
    sub.add_parser("relay", help="Run the outbox relay worker until SIGTERM")
    args = parser.parse_args(argv)

    if args.command == "relay":
        outbox_relay.main()
        return 0
    settings = Settings()
    return _seed(settings) if args.command == "seed" else _reconcile(settings)


if __name__ == "__main__":
    sys.exit(main())
