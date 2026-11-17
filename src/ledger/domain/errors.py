"""Errors raised by the core of the application.

The core never mentions HTTP. Every error belongs to a *category* (a base class),
and the API layer maps categories to status codes. That keeps the same rules usable
from the HTTP API, the CLI, background workers and tests.
"""

from __future__ import annotations

from typing import ClassVar


class LedgerError(Exception):
    """Base class. An uncategorised LedgerError signals a bug and surfaces as a 500."""

    code: ClassVar[str] = "internal-error"
    title: ClassVar[str] = "Internal error"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.title
        super().__init__(self.detail)


# --- Categories --------------------------------------------------------------------


class BusinessRuleViolation(LedgerError):
    """The request is well-formed but breaks a business rule."""


class NotFound(LedgerError):
    """The resource does not exist, or the caller is not allowed to know it exists."""


class Conflict(LedgerError):
    """The request conflicts with the current state of a resource."""


class AuthenticationFailed(LedgerError):
    """The caller could not be identified."""


class InvalidRequest(LedgerError):
    """The request is malformed in a way schema validation cannot catch."""


class RateLimited(LedgerError):
    code = "rate-limited"
    title = "Too many requests"

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limit exceeded; retry in {retry_after_seconds}s")


# --- Business rules ----------------------------------------------------------------


class InvalidAmount(BusinessRuleViolation):
    code = "invalid-amount"
    title = "Invalid amount"


class UnsupportedCurrency(BusinessRuleViolation):
    code = "unsupported-currency"
    title = "Unsupported currency"


class CurrencyMismatch(BusinessRuleViolation):
    code = "currency-mismatch"
    title = "Currency mismatch"


class InsufficientFunds(BusinessRuleViolation):
    code = "insufficient-funds"
    title = "Insufficient funds"


class SameAccountTransfer(BusinessRuleViolation):
    code = "same-account-transfer"
    title = "Source and destination must differ"


class IdempotencyKeyReused(BusinessRuleViolation):
    code = "idempotency-key-reused"
    title = "Idempotency key reused with a different request"


class UnbalancedEntry(LedgerError):
    """Raised when code tries to record postings that do not sum to zero. Always a bug."""

    code = "unbalanced-entry"
    title = "Journal entry does not balance"


# --- Lookups, conflicts, auth, malformed input ----------------------------------------


class AccountNotFound(NotFound):
    code = "account-not-found"
    title = "Account not found"


class TransactionNotFound(NotFound):
    code = "transaction-not-found"
    title = "Transaction not found"


class EmailAlreadyRegistered(Conflict):
    code = "email-already-registered"
    title = "Email already registered"


class InvalidCredentials(AuthenticationFailed):
    code = "invalid-credentials"
    title = "Invalid email or password"


class InvalidToken(AuthenticationFailed):
    code = "invalid-token"
    title = "Missing, invalid or expired access token"


class InvalidCursor(InvalidRequest):
    code = "invalid-cursor"
    title = "Invalid pagination cursor"


class InvalidIdempotencyKey(InvalidRequest):
    code = "invalid-idempotency-key"
    title = "A valid Idempotency-Key header is required"
