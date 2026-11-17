"""Small pure helpers: cursors, configuration, webhook signing and retry backoff."""

import pytest
from pydantic import SecretStr, ValidationError

from ledger.config import Settings
from ledger.domain.errors import InvalidCursor
from ledger.services.idempotency import fingerprint_request
from ledger.services.pagination import decode_cursor, encode_cursor
from ledger.workers.outbox_relay import backoff_seconds, sign_payload, verify_signature


def test_cursor_round_trip() -> None:
    assert decode_cursor(encode_cursor(12345)) == 12345


@pytest.mark.parametrize("cursor", ["%%%", "bm9wZQ", "cG9zdGluZzphYmM", "//8"])
def test_tampered_cursors_are_rejected(cursor: str) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(cursor)


def test_production_refuses_the_development_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(environment="production")


def test_production_accepts_a_strong_secret() -> None:
    settings = Settings(environment="production", jwt_secret=SecretStr("x" * 48))
    assert settings.environment == "production"


def test_currency_list_is_normalised_and_empty_webhook_means_none() -> None:
    settings = Settings(supported_currencies=("usd", " eur"), webhook_url="")
    assert settings.supported_currencies == ("USD", "EUR")
    assert settings.webhook_url is None


def test_request_fingerprint_ignores_key_order_but_not_values() -> None:
    a = fingerprint_request("post", "/v1/transfers", {"amount": 1, "to": "x"})
    b = fingerprint_request("POST", "/v1/transfers", {"to": "x", "amount": 1})
    c = fingerprint_request("POST", "/v1/transfers", {"to": "x", "amount": 2})
    assert a == b
    assert a != c


def test_webhook_signatures_verify_only_the_exact_body_and_secret() -> None:
    signature = sign_payload("s3cret", b'{"id":1}')
    assert signature.startswith("sha256=")
    assert verify_signature("s3cret", b'{"id":1}', signature)
    assert not verify_signature("s3cret", b'{"id":2}', signature)
    assert not verify_signature("other", b'{"id":1}', signature)


def test_backoff_doubles_and_is_capped() -> None:
    no_jitter = lambda: 0.0  # noqa: E731
    delays = [backoff_seconds(n, jitter=no_jitter) for n in (1, 2, 3, 4, 20)]
    assert delays == [2.0, 4.0, 8.0, 16.0, 300.0]


def test_backoff_jitter_adds_at_most_ten_percent() -> None:
    assert backoff_seconds(3, jitter=lambda: 1.0) == pytest.approx(8.8)
