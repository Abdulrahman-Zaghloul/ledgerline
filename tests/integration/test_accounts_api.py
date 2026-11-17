import uuid
from collections.abc import Callable

from fastapi.testclient import TestClient

from tests.integration.conftest import ApiUser


def test_open_account_starts_at_zero(make_user: Callable[..., ApiUser]) -> None:
    user = make_user()
    response = user.client.post(
        "/v1/accounts", json={"name": "Travel", "currency": "eur"}, headers=user.headers
    )
    assert response.status_code == 201
    account = response.json()
    assert account["currency"] == "EUR"
    assert account["balance"] == 0
    assert account["balance_display"] == "0.00 EUR"
    assert response.headers["location"] == f"/v1/accounts/{account['id']}"


def test_currency_must_be_known_and_enabled(make_user: Callable[..., ApiUser]) -> None:
    user = make_user()
    for currency in ("XYZ", "CHF"):  # unknown to ISO table / known but not enabled
        response = user.client.post(
            "/v1/accounts", json={"name": "W", "currency": currency}, headers=user.headers
        )
        assert response.status_code == 422
        assert response.json()["type"] == "/problems/unsupported-currency"


def test_users_only_see_their_own_accounts(make_user: Callable[..., ApiUser]) -> None:
    alice, bob = make_user(), make_user()
    alice_account = alice.open_account()
    bob.open_account()

    listed = alice.client.get("/v1/accounts", headers=alice.headers).json()
    assert [a["id"] for a in listed] == [alice_account["id"]]

    peek = bob.client.get(f"/v1/accounts/{alice_account['id']}", headers=bob.headers)
    assert peek.status_code == 404  # not 403: don't confirm the account exists


def test_unknown_account_is_not_found(make_user: Callable[..., ApiUser]) -> None:
    user = make_user()
    response = user.client.get(f"/v1/accounts/{uuid.uuid4()}", headers=user.headers)
    assert response.status_code == 404


def test_statement_pages_newest_first_without_gaps_or_repeats(
    make_user: Callable[..., ApiUser],
) -> None:
    user = make_user()
    account = user.open_account()
    for amount in (100, 200, 300, 400, 500):
        assert user.deposit(account["id"], amount).status_code == 201

    seen: list[int] = []
    cursor = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = user.client.get(
            f"/v1/accounts/{account['id']}/statement", params=params, headers=user.headers
        ).json()
        seen += [item["amount"] for item in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert seen == [500, 400, 300, 200, 100]


def test_statement_rejects_a_tampered_cursor(make_user: Callable[..., ApiUser]) -> None:
    user = make_user()
    account = user.open_account()
    response = user.client.get(
        f"/v1/accounts/{account['id']}/statement",
        params={"cursor": "not-a-cursor"},
        headers=user.headers,
    )
    assert response.status_code == 400
    assert response.json()["type"] == "/problems/invalid-cursor"


def test_transaction_is_visible_to_both_parties_only(
    client: TestClient, make_user: Callable[..., ApiUser]
) -> None:
    alice, bob, eve = make_user(), make_user(), make_user()
    a, b = alice.open_account(), bob.open_account()
    alice.deposit(a["id"], 1000)
    transaction_id = alice.transfer(a["id"], b["id"], 400).json()["id"]

    for party in (alice, bob):
        response = client.get(f"/v1/transactions/{transaction_id}", headers=party.headers)
        assert response.status_code == 200
    assert client.get(f"/v1/transactions/{transaction_id}", headers=eve.headers).status_code == 404
