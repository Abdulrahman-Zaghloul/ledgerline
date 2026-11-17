"""Application factory.

Run with:  uvicorn --factory ledger.main:create_app
A factory (instead of a module-level ``app``) lets tests build isolated apps with
their own settings, and keeps imports free of side effects.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ledger import __version__
from ledger.api.errors import register_exception_handlers
from ledger.api.middleware import RequestContextMiddleware
from ledger.api.rate_limit import TokenBucketLimiter
from ledger.api.routes import api_v1
from ledger.api.routes.health import router as health_router
from ledger.config import Settings, get_settings
from ledger.db.session import build_engine, build_session_factory
from ledger.observability.logging import configure_logging
from ledger.repositories.accounts import AccountRepository

DESCRIPTION = """
A double-entry wallet and payments ledger.

* **Money is integers.** Amounts are minor units (cents); no floating point anywhere.
* **Every movement balances.** Each transaction's postings sum to zero, enforced by the database.
* **Retries are safe.** Money-moving requests require an `Idempotency-Key` header.
* **No double-spending.** Concurrent requests are serialised with ordered row locks.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        with session_factory() as session:
            AccountRepository(session).ensure_system_accounts(settings.supported_currencies)
            session.commit()
        yield
        engine.dispose()

    app = FastAPI(
        title="Ledgerline",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        license_info={"name": "MIT"},
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.rate_limiter = TokenBucketLimiter(
        settings.rate_limit_capacity, settings.rate_limit_refill_per_second
    )
    app.state.login_rate_limiter = TokenBucketLimiter(
        settings.login_rate_limit_capacity, settings.login_rate_limit_refill_per_second
    )

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_v1)
    return app
