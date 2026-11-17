"""Database tables, mapped with SQLAlchemy 2.0.

The schema enforces the ledger's invariants itself (CHECK constraints, a composite
foreign key, and triggers added in the migration), so a bug in application code
cannot silently corrupt balances. The application checks rules first for good error
messages; the database is the last line of defence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Deterministic constraint names make migrations reviewable and reversible.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = _created_at()

    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012


class Account(Base):
    """A wallet owned by a user, or a system account owned by the platform.

    User accounts carry a cached ``balance_minor`` that is updated in the same
    database transaction as their postings. System accounts leave it NULL: their
    balance is derived from postings on demand, which keeps them from becoming a
    lock hot-spot (ADR 0006).
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    system_code: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    balance_minor: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint("id", "currency"),  # target of the postings composite FK
        CheckConstraint("(owner_id IS NULL) <> (system_code IS NULL)", name="owner_xor_system"),
        CheckConstraint(
            "(owner_id IS NULL) = (balance_minor IS NULL)", name="balance_tracked_for_users"
        ),
        CheckConstraint("balance_minor >= 0", name="balance_not_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        Index(
            "uq_accounts_system_code_currency",
            "system_code",
            "currency",
            unique=True,
            postgresql_where=text("system_code IS NOT NULL"),
        ),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012


class LedgerTransaction(Base):
    """One business event (deposit, withdrawal, transfer). Append-only."""

    __tablename__ = "ledger_transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(String(140))
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()

    postings: Mapped[list[Posting]] = relationship(
        back_populates="transaction", lazy="selectin", order_by="Posting.id"
    )

    __table_args__ = (
        CheckConstraint("kind IN ('deposit', 'withdrawal', 'transfer')", name="kind_valid"),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012


class Posting(Base):
    """One line of a ledger transaction: a signed amount against one account. Append-only."""

    __tablename__ = "postings"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _created_at()

    transaction: Mapped[LedgerTransaction] = relationship(back_populates="postings")

    __table_args__ = (
        # A posting's currency must equal its account's currency: enforced by the database.
        ForeignKeyConstraint(
            ["account_id", "currency"],
            ["accounts.id", "accounts.currency"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount_minor <> 0", name="amount_not_zero"),
        Index("ix_postings_account_id_id", "account_id", "id"),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012


class IdempotencyKey(Base):
    """Remembers the response to a money-moving request so retries are safe."""

    __tablename__ = "idempotency_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (Index("ix_idempotency_keys_created_at", "created_at"),)


class OutboxEvent(Base):
    """An event written in the same transaction as the change it describes (ADR 0005)."""

    __tablename__ = "outbox_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[uuid.UUID] = mapped_column()
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index(
            "ix_outbox_events_pending",
            "next_attempt_at",
            postgresql_where=text("published_at IS NULL AND dead_lettered_at IS NULL"),
        ),
    )
