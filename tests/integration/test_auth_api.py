from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ledger.config import Settings
from tests.integration.conftest import PASSWORD, ApiUser


def test_register_returns_the_user_without_secrets(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/register", json={"email": "  Alice@Example.COM ", "password": PASSWORD}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "alice@example.com"
    assert set(body) == {"id", "email", "created_at"}


def test_duplicate_email_is_a_conflict_regardless_of_case(client: TestClient) -> None:
    client.post("/v1/auth/register", json={"email": "bob@example.com", "password": PASSWORD})
    response = client.post(
        "/v1/auth/register", json={"email": "BOB@example.com", "password": PASSWORD}
    )
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "/problems/email-already-registered"


def test_weak_passwords_and_unknown_fields_fail_validation(client: TestClient) -> None:
    short = client.post("/v1/auth/register", json={"email": "c@example.com", "password": "short"})
    assert short.status_code == 422
    assert short.json()["errors"][0]["location"] == ["body", "password"]

    extra = client.post(
        "/v1/auth/register",
        json={"email": "c@example.com", "password": PASSWORD, "is_admin": True},
    )
    assert extra.status_code == 422


def test_login_and_me(client: TestClient, make_user: Callable[..., ApiUser]) -> None:
    user = make_user()
    response = client.get("/v1/me", headers=user.headers)
    assert response.status_code == 200
    assert response.json()["email"] == user.email


def test_wrong_password_and_unknown_email_look_identical(
    client: TestClient, make_user: Callable[..., ApiUser]
) -> None:
    user = make_user()
    wrong = client.post("/v1/auth/token", json={"email": user.email, "password": "nope" * 4})
    unknown = client.post(
        "/v1/auth/token", json={"email": "ghost@example.com", "password": "nope" * 4}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]
    assert wrong.headers["www-authenticate"] == "Bearer"


def _token(settings: Settings, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "00000000-0000-0000-0000-000000000000",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def test_protected_routes_reject_missing_and_bad_tokens(
    client: TestClient, make_user: Callable[..., ApiUser], settings: Settings
) -> None:
    user = make_user()
    expired = _token(settings, sub=user.id, exp=datetime.now(UTC) - timedelta(seconds=1))
    wrong_audience = _token(settings, sub=user.id, aud="someone-else")
    forged = jwt.encode({"sub": user.id}, "not-the-real-secret-" * 3, algorithm="HS256")
    unsigned = jwt.encode({"sub": user.id}, key="", algorithm="none")
    deleted_user = _token(settings)

    assert client.get("/v1/me").status_code == 401
    for token in (expired, wrong_audience, forged, unsigned, deleted_user, "garbage"):
        response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, token


def test_login_is_rate_limited(make_app: Callable[..., FastAPI]) -> None:
    app = make_app(login_rate_limit_capacity=2, login_rate_limit_refill_per_second=0.01)
    with TestClient(app) as client:
        attempt = {"email": "x@example.com", "password": "wrong-password"}
        statuses = [client.post("/v1/auth/token", json=attempt).status_code for _ in range(3)]
        assert statuses == [401, 401, 429]
        limited = client.post("/v1/auth/token", json=attempt)
        assert int(limited.headers["retry-after"]) >= 1
