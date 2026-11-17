"""Dependency injection: small functions FastAPI calls to give each handler what it needs."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ledger.api.rate_limit import TokenBucketLimiter
from ledger.config import Settings
from ledger.db.models import User
from ledger.domain.errors import InvalidIdempotencyKey, InvalidToken, RateLimited
from ledger.services.auth import AuthService

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /v1/auth/token")
_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{1,255}$")  # 1-255 visible ASCII characters


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session(request: Request) -> Iterator[Session]:
    """One database session per request, always closed (and rolled back if uncommitted)."""
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]


def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise InvalidToken()
    return AuthService(session, settings).user_from_token(credentials.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_idempotency_key(
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Unique per logical operation (a UUID works well). Retries reuse it.",
        ),
    ] = None,
) -> str:
    if idempotency_key is None or not _IDEMPOTENCY_KEY.match(idempotency_key):
        raise InvalidIdempotencyKey()
    return idempotency_key


IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]


def enforce_user_rate_limit(request: Request, user: CurrentUser) -> None:
    limiter: TokenBucketLimiter = request.app.state.rate_limiter
    decision = limiter.consume(f"user:{user.id}")
    if not decision.allowed:
        raise RateLimited(decision.retry_after_seconds)


def enforce_login_rate_limit(request: Request) -> None:
    """Throttle credential guessing per client address, with a stricter budget."""
    limiter: TokenBucketLimiter = request.app.state.login_rate_limiter
    client = request.client.host if request.client else "unknown"
    decision = limiter.consume(f"login:{client}")
    if not decision.allowed:
        raise RateLimited(decision.retry_after_seconds)
