"""Registration, login and access tokens."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ledger.config import Settings
from ledger.db.models import User
from ledger.domain.errors import EmailAlreadyRegistered, InvalidCredentials, InvalidToken
from ledger.repositories.users import UserRepository

_JWT_ALGORITHM = "HS256"
_default_hasher = PasswordHasher()  # Argon2id with the library's recommended parameters
# Verified when the email is unknown, so "no such user" takes as long as "wrong password"
# and response timing cannot be used to discover which emails are registered.
_DUMMY_HASH = _default_hasher.hash("timing-equaliser-not-a-real-password")


@dataclass(frozen=True, slots=True)
class IssuedToken:
    access_token: str
    expires_in: int


def normalise_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(
        self, session: Session, settings: Settings, hasher: PasswordHasher | None = None
    ) -> None:
        self._users = UserRepository(session)
        self._session = session
        self._settings = settings
        self._hasher = hasher or _default_hasher

    def register(self, email: str, password: str) -> User:
        email = normalise_email(email)
        if self._users.get_by_email(email) is not None:
            raise EmailAlreadyRegistered()
        user = User(id=uuid.uuid4(), email=email, password_hash=self._hasher.hash(password))
        try:
            with self._session.begin_nested():  # savepoint: a lost race must not poison the tx
                self._users.add(user)
        except IntegrityError:
            raise EmailAlreadyRegistered() from None
        return user

    def authenticate(self, email: str, password: str) -> User:
        user = self._users.get_by_email(normalise_email(email))
        try:
            self._hasher.verify(user.password_hash if user else _DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            raise InvalidCredentials() from None
        if user is None:
            raise InvalidCredentials()
        if self._hasher.check_needs_rehash(user.password_hash):
            user.password_hash = self._hasher.hash(password)
        return user

    def issue_token(self, user: User) -> IssuedToken:
        now = datetime.now(UTC)
        ttl = timedelta(minutes=self._settings.access_token_ttl_minutes)
        claims = {
            "sub": str(user.id),
            "iss": self._settings.jwt_issuer,
            "aud": self._settings.jwt_audience,
            "iat": now,
            "exp": now + ttl,
            "jti": uuid.uuid4().hex,
        }
        token = jwt.encode(
            claims, self._settings.jwt_secret.get_secret_value(), algorithm=_JWT_ALGORITHM
        )
        return IssuedToken(access_token=token, expires_in=int(ttl.total_seconds()))

    def user_from_token(self, token: str) -> User:
        try:
            claims = jwt.decode(
                token,
                self._settings.jwt_secret.get_secret_value(),
                algorithms=[_JWT_ALGORITHM],  # pinned: never let the token choose its algorithm
                audience=self._settings.jwt_audience,
                issuer=self._settings.jwt_issuer,
                options={"require": ["sub", "exp", "iat", "iss", "aud"]},
            )
            user_id = uuid.UUID(claims["sub"])
        except (jwt.PyJWTError, ValueError):
            raise InvalidToken() from None
        user = self._users.get(user_id)
        if user is None:
            raise InvalidToken()
        return user
