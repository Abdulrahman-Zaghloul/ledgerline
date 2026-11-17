import uuid
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from ledger.services.reconciliation import reconcile
from tests.integration.conftest import ApiUser, system_account


def test_deposit_credits_the_wallet_with_a_balanced_transaction(
    make_user: Callable[..., ApiUser],
) -> None:
    user = make_user()
    account = user.open_account()
    response = user.deposit(account["id"], 10_000)

    assert response.status_code == 201
    transaction = response.json()
    assert transaction["kind"] == "deposit"
    assert sum(p["amount"] for p in transaction["postings"]) == 0
    assert response.headers["location"] == f"/v1/transactions/{transaction['id']}"
    assert user.balance(account["id"]) == 10_000


def test_withdrawal_cannot_overdraw(make_user: Callable[..., ApiUser]) -> None:
    user = make_user()
    account = user.open_account()
    user.deposit(account["id"], 1_000)

    assert user.withdraw(account["id"], 400).status_code == 201
    refused = user.withdraw(account["id"], 601)
    assert refused.status_code == 422
    assert refused.json()["type"] == "/problems/insufficient-funds"
    assert user.balance(account["id"]) == 600


def test_transfer_moves_money_between_users(make_user: Callable[..., ApiUser]) -> None:
    alice, bob = make_user(), make_user()
    a, b = alice.open_account(), bob.open_account()
    alice.deposit(a["id"], 5_000)

    response = alice.transfer(a["id"], b["id"], 1_250, description="Concert tickets")

    assert response.status_code == 201
    assert response.json()["description"] == "Concert tickets"
    assert alice.balance(a["id"]) == 3_750
    assert bob.balance(b["id"]) == 1_250


def test_transfer_fee_goes_to_the_platform_fee_account(
    make_app: Callable[..., FastAPI],
    make_user: Callable[..., ApiUser],
    session_factory: sessionmaker[Session],
) -> None:
    with TestClient(make_app(transfer_fee_bps=100)) as fee_client:
        alice, bob = make_user(on=fee_client), make_user(on=fee_client)
        a, b = alice.open_account(), bob.open_account()
        alice.deposit(a["id"], 10_000)

        postings = alice.transfer(a["id"], b["id"], 5_000).json()["postings"]

        with session_factory() as session:
            fees_id = str(system_account(session, "fees").id)
        assert {p["account_id"]: p["amount"] for p in postings} == {
            a["id"]: -5_050,
            b["id"]: 5_000,
            fees_id: 50,
        }
        assert alice.balance(a["id"]) == 4_950


def test_transfer_business_rules(make_user: Callable[..., ApiUser]) -> None:
    alice, bob = make_user(), make_user()
    usd, eur = alice.open_account("USD"), alice.open_account("EUR")
    bob_usd = bob.open_account("USD")
    alice.deposit(usd["id"], 1_000)

    same = alice.transfer(usd["id"], usd["id"], 1)
    assert same.json()["type"] == "/problems/same-account-transfer"
    mismatch = alice.transfer(usd["id"], eur["id"], 1)
    assert mismatch.json()["type"] == "/problems/currency-mismatch"
    not_mine = alice.transfer(bob_usd["id"], usd["id"], 1)
    assert not_mine.status_code == 404
    nowhere = alice.transfer(usd["id"], str(uuid.uuid4()), 1)
    assert nowhere.status_code == 404
    assert alice.balance(usd["id"]) == 1_000


def test_system_accounts_cannot_be_targeted_directly(
    make_user: Callable[..., ApiUser], session_factory: sessionmaker[Session]
) -> None:
    user = make_user()
    account = user.open_account()
    user.deposit(account["id"], 1_000)
    with session_factory() as session:
        fees_id = str(system_account(session, "fees").id)
    assert user.transfer(account["id"], fees_id, 100).status_code == 404
    assert user.deposit(fees_id, 100).status_code == 404


@pytest.mark.parametrize("amount", [0, -5, 10.5, "100", 10**12 + 1, None])
def test_amount_must_be_a_positive_integer_within_limits(
    make_user: Callable[..., ApiUser], amount: object
) -> None:
    user = make_user()
    account = user.open_account()
    response = user.client.post(
        "/v1/deposits",
        json={"account_id": account["id"], "amount": amount},
        headers={**user.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 422
    assert response.json()["type"] == "/problems/validation-error"


@pytest.mark.parametrize("key", [None, "", "has space", "é".encode(), "k" * 256])
def test_money_movement_requires_a_valid_idempotency_key(
    make_user: Callable[..., ApiUser], key: str | bytes | None
) -> None:
    user = make_user()
    account = user.open_account()
    headers: dict[str, str | bytes] = dict(user.headers)
    if key is not None:
        headers["Idempotency-Key"] = key
    response = user.client.post(
        "/v1/deposits", json={"account_id": account["id"], "amount": 100}, headers=headers
    )
    assert response.status_code == 400
    assert response.json()["type"] == "/problems/invalid-idempotency-key"


def test_money_movement_is_rate_limited_per_user(
    make_app: Callable[..., FastAPI], make_user: Callable[..., ApiUser]
) -> None:
    with TestClient(make_app(rate_limit_capacity=2, rate_limit_refill_per_second=0.01)) as client:
        user = make_user(on=client)
        account = user.open_account()
        statuses = [user.deposit(account["id"], 100).status_code for _ in range(3)]
        assert statuses == [201, 201, 429]


def test_books_balance_after_a_busy_day(
    make_user: Callable[..., ApiUser], session_factory: sessionmaker[Session]
) -> None:
    users = [make_user() for _ in range(3)]
    accounts = [u.open_account() for u in users]
    for user, account in zip(users, accounts, strict=True):
        user.deposit(account["id"], 10_000)
    for i, (user, account) in enumerate(zip(users, accounts, strict=True)):
        target = accounts[(i + 1) % len(accounts)]
        user.transfer(account["id"], target["id"], 1_234)
        user.withdraw(account["id"], 99)

    with session_factory() as session:
        report = reconcile(session)
    assert report.ok
    assert report.accounts_checked == 3
