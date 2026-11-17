"""Health checks, request correlation, error shapes and metrics."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ledger.domain.errors import LedgerError
from tests.integration.conftest import ApiUser


def test_liveness_and_readiness(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ok"}


def test_readiness_fails_when_the_database_is_unreachable(
    make_app: Callable[..., FastAPI],
) -> None:
    app = make_app(database_url="postgresql+psycopg://postgres:postgres@localhost:1/nowhere")
    response = TestClient(app).get("/health/ready")  # no `with`: skip startup seeding
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_request_ids_are_echoed_when_safe_and_replaced_when_not(client: TestClient) -> None:
    echoed = client.get("/health/live", headers={"X-Request-ID": "trace-abc.123"})
    assert echoed.headers["x-request-id"] == "trace-abc.123"

    replaced = client.get("/health/live", headers={"X-Request-ID": "bad id\nwith newline"})
    assert replaced.headers["x-request-id"] != "bad id\nwith newline"
    assert len(replaced.headers["x-request-id"]) == 32


def test_errors_carry_the_request_id(client: TestClient) -> None:
    response = client.get("/v1/me", headers={"X-Request-ID": "find-me-in-the-logs"})
    assert response.json()["request_id"] == "find-me-in-the-logs"


def test_unknown_routes_return_problem_json(client: TestClient) -> None:
    response = client.get("/v1/nope")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


def test_unexpected_errors_are_hidden_behind_a_generic_500(
    make_app: Callable[..., FastAPI],
) -> None:
    app = make_app()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("database password is hunter2")

    @app.get("/bug")
    def bug() -> None:
        raise LedgerError("uncategorised")

    with TestClient(app) as client:
        for path in ("/boom", "/bug"):
            response = client.get(path)
            assert response.status_code == 500
            assert "hunter2" not in response.text
            assert response.json()["type"] == "/problems/internal-error"
            assert response.headers["x-request-id"]


def test_metrics_use_route_templates_not_raw_ids(
    client: TestClient, make_user: Callable[..., ApiUser]
) -> None:
    user = make_user()
    account = user.open_account()
    user.deposit(account["id"], 100)
    user.balance(account["id"])

    metrics = client.get("/metrics").text
    assert 'route="/v1/accounts/{account_id}"' in metrics
    assert account["id"] not in metrics
    assert 'ledger_transactions_total{kind="deposit"}' in metrics


def test_unmatched_paths_share_one_metric_label(client: TestClient) -> None:
    client.get("/wp-admin/setup.php")
    metrics = client.get("/metrics").text
    assert "wp-admin" not in metrics
    assert 'route="unmatched"' in metrics


def test_openapi_documents_the_idempotency_header(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    parameters = spec["paths"]["/v1/transfers"]["post"]["parameters"]
    assert any(p["name"] == "Idempotency-Key" for p in parameters)
