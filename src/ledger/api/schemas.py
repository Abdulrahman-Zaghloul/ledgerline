"""Request and response shapes: the public contract of the API.

Kept separate from the database models so the schema can change without breaking
clients, and so internal columns (like password hashes) can never leak by accident.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ledger.db.models import Account, LedgerTransaction, Posting, User
from ledger.domain.money import Money

MAX_AMOUNT_MINOR = 10**12

Amount = Annotated[
    int,
    Field(
        strict=True,
        gt=0,
        le=MAX_AMOUNT_MINOR,
        description="Minor units of the account currency (USD cents: 1050 = $10.50)",
        examples=[1050],
    ),
]
Description = Annotated[str | None, Field(default=None, max_length=140, examples=["Dinner split"])]


class StrictRequest(BaseModel):
    """Unknown fields are rejected, so a typo like ``ammount`` fails loudly, not silently."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Problem(BaseModel):
    """RFC 9457 problem details: one predictable error shape for every failure."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    request_id: str | None = None


# --- Auth ------------------------------------------------------------------------------


class RegisterRequest(StrictRequest):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(StrictRequest):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - a scheme name, not a secret
    expires_in: int = Field(description="Seconds until the token expires")


class UserOut(BaseModel):
    id: UUID
    email: str
    created_at: datetime

    @classmethod
    def from_model(cls, user: User) -> UserOut:
        return cls(id=user.id, email=user.email, created_at=user.created_at)


# --- Accounts ----------------------------------------------------------------------------


class AccountCreate(StrictRequest):
    name: str = Field(min_length=1, max_length=64, examples=["Everyday wallet"])
    currency: str = Field(pattern=r"^[A-Za-z]{3}$", examples=["USD"])


class AccountOut(BaseModel):
    id: UUID
    name: str
    currency: str
    balance: int = Field(description="Balance in minor units")
    balance_display: str = Field(examples=["10.50 USD"])
    created_at: datetime

    @classmethod
    def from_model(cls, account: Account) -> AccountOut:
        balance = account.balance_minor or 0
        return cls(
            id=account.id,
            name=account.name,
            currency=account.currency,
            balance=balance,
            balance_display=str(Money.of(balance, account.currency)),
            created_at=account.created_at,
        )


class StatementLineOut(BaseModel):
    posting_id: int
    transaction_id: UUID
    kind: str
    description: str | None
    amount: int = Field(description="Signed: positive = money in, negative = money out")
    currency: str
    created_at: datetime


class StatementOut(BaseModel):
    account_id: UUID
    items: list[StatementLineOut]
    next_cursor: str | None = Field(description="Pass as ?cursor= to fetch the next page")


# --- Money movement ------------------------------------------------------------------------


class DepositRequest(StrictRequest):
    account_id: UUID
    amount: Amount
    description: Description = None


class WithdrawalRequest(StrictRequest):
    account_id: UUID
    amount: Amount
    description: Description = None


class TransferRequest(StrictRequest):
    from_account_id: UUID
    to_account_id: UUID
    amount: Amount
    description: Description = None


class PostingOut(BaseModel):
    account_id: UUID
    amount: int
    currency: str

    @classmethod
    def from_model(cls, posting: Posting) -> PostingOut:
        return cls(
            account_id=posting.account_id, amount=posting.amount_minor, currency=posting.currency
        )


class TransactionOut(BaseModel):
    id: UUID
    kind: str
    description: str | None
    created_at: datetime
    postings: list[PostingOut]

    @classmethod
    def from_model(cls, transaction: LedgerTransaction) -> TransactionOut:
        return cls(
            id=transaction.id,
            kind=transaction.kind,
            description=transaction.description,
            created_at=transaction.created_at,
            postings=[PostingOut.from_model(p) for p in transaction.postings],
        )


class HealthOut(BaseModel):
    status: Literal["ok", "unavailable"]
