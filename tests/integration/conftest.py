"""Fixtures for tests that run against a real PostgreSQL database.

The schema is built by running the real Alembic migrations (down to base, then up to
head), so every test run also proves the migrations work in both directions.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from ledger.config import Settings
from ledger.db.models import Account, User
from ledger.db.session import build_engine, build_session_factory
from ledger.main import create_app
from ledger.repositories.accounts import AccountRepository
from tests.conftest import TEST_DATABASE_URL

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "correct horse battery staple"
LEDGER_TABLES = "users, accounts, ledger_transactions, postings, idempotency_keys, outbox_events"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url=TEST_DATABASE_URL,
        log_json=False,
        log_level="WARNING",
        rate_limit_capacity=10_000,
        rate_limit_refill_per_second=10_000,
        login_rate_limit_capacity=10_000,
        login_rate_limit_refill_per_second=10_000,
        db_pool_size=20,
        db_max_overflow=30,
    )


@pytest.fixture(scope="session")
def engine(settings: Settings) -> Iterator[Engine]:
    config = AlembicConfig(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = build_engine(settings)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return build_session_factory(engine)


@pytest.fixture(autouse=True)
def clean_database(
    engine: Engine, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    # TRUNCATE bypasses the append-only row triggers, which is what test cleanup needs.
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {LEDGER_TABLES} RESTART IDENTITY CASCADE"))
    with session_factory() as session:
        AccountRepository(session).ensure_system_accounts(settings.supported_currencies)
        session.commit()


@pytest.fixture
def make_app(settings: Settings) -> Callable[..., FastAPI]:
    def _make(**overrides: Any) -> FastAPI:
        return create_app(settings.model_copy(update=overrides))

    return _make


@pytest.fixture
def client(make_app: Callable[..., FastAPI]) -> Iterator[TestClient]:
    with TestClient(make_app()) as test_client:
        yield test_client


@dataclass
class ApiUser:
    """A registered, logged-in user with convenience methods for common calls."""

    client: TestClient
    id: str
    email: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def open_account(self, currency: str = "USD", name: str = "Wallet") -> dict[str, Any]:
        response = self.client.post(
            "/v1/accounts", json={"name": name, "currency": currency}, headers=self.headers
        )
        assert response.status_code == 201, response.text
        return dict(response.json())

    def balance(self, account_id: str) -> int:
        response = self.client.get(f"/v1/accounts/{account_id}", headers=self.headers)
        assert response.status_code == 200, response.text
        return int(response.json()["balance"])

    def _post_money(self, path: str, body: dict[str, Any], key: str | None) -> Response:
        headers = {**self.headers, "Idempotency-Key": key or uuid.uuid4().hex}
        return self.client.post(path, json=body, headers=headers)

    def deposit(self, account_id: str, amount: int, key: str | None = None) -> Response:
        return self._post_money("/v1/deposits", {"account_id": account_id, "amount": amount}, key)

    def withdraw(self, account_id: str, amount: int, key: str | None = None) -> Response:
        return self._post_money(
            "/v1/withdrawals", {"account_id": account_id, "amount": amount}, key
        )

    def transfer(
        self,
        source: str,
        destination: str,
        amount: int,
        key: str | None = None,
        description: str | None = None,
    ) -> Response:
        body: dict[str, Any] = {
            "from_account_id": source,
            "to_account_id": destination,
            "amount": amount,
        }
        if description is not None:
            body["description"] = description
        return self._post_money("/v1/transfers", body, key)


@pytest.fixture
def make_user(client: TestClient) -> Callable[..., ApiUser]:
    counter = itertools.count(1)

    def _make(email: str | None = None, *, on: TestClient | None = None) -> ApiUser:
        http = on or client
        email = email or f"user{next(counter)}-{uuid.uuid4().hex[:6]}@example.com"
        registered = http.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
        assert registered.status_code == 201, registered.text
        login = http.post("/v1/auth/token", json={"email": email, "password": PASSWORD})
        assert login.status_code == 200, login.text
        return ApiUser(http, registered.json()["id"], email, login.json()["access_token"])

    return _make


# --- Service-level helpers (for concurrency tests that bypass HTTP) ---------------------


def create_user(session: Session) -> User:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com", password_hash="x")
    session.add(user)
    session.flush()
    return user


def create_wallet(session: Session, owner: User, currency: str = "USD") -> Account:
    account = Account(
        id=uuid.uuid4(), owner_id=owner.id, name="Wallet", currency=currency, balance_minor=0
    )
    session.add(account)
    session.flush()
    return account


def system_account(session: Session, code: str, currency: str = "USD") -> Account:
    return AccountRepository(session).get_system_account(code, currency)
