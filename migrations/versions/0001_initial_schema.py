"""Initial schema: users, accounts, double-entry ledger, idempotency keys, outbox.

Revision ID: 0001
Revises:
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Ledger rows are facts: once written they are never updated or deleted. Corrections
# are made with new, compensating transactions, like an accountant's ink ledger.
APPEND_ONLY_FUNCTION = """
CREATE FUNCTION forbid_ledger_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ledger table "%" is append-only; % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

# Checked at COMMIT (deferred), after every posting of the transaction has been inserted.
BALANCED_FUNCTION = """
CREATE FUNCTION check_transaction_balanced() RETURNS trigger AS $$
DECLARE
    imbalance RECORD;
BEGIN
    SELECT currency, SUM(amount_minor) AS total INTO imbalance
    FROM postings
    WHERE transaction_id = NEW.transaction_id
    GROUP BY currency
    HAVING SUM(amount_minor) <> 0
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION 'ledger transaction % is unbalanced: % % left over',
            NEW.transaction_id, imbalance.total, imbalance.currency
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("system_code", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("balance_minor", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_accounts_owner_id_users", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("id", "currency", name="uq_accounts_id_currency"),
        sa.CheckConstraint(
            "(owner_id IS NULL) <> (system_code IS NULL)", name="ck_accounts_owner_xor_system"
        ),
        sa.CheckConstraint(
            "(owner_id IS NULL) = (balance_minor IS NULL)",
            name="ck_accounts_balance_tracked_for_users",
        ),
        sa.CheckConstraint("balance_minor >= 0", name="ck_accounts_balance_not_negative"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_accounts_currency_format"),
    )
    op.create_index("ix_accounts_owner_id", "accounts", ["owner_id"])
    op.create_index(
        "uq_accounts_system_code_currency",
        "accounts",
        ["system_code", "currency"],
        unique=True,
        postgresql_where=sa.text("system_code IS NOT NULL"),
    )

    op.create_table(
        "ledger_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("description", sa.String(length=140), nullable=True),
        sa.Column("initiated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ledger_transactions"),
        sa.ForeignKeyConstraint(
            ["initiated_by"], ["users.id"], name="fk_ledger_transactions_initiated_by_users"
        ),
        sa.CheckConstraint(
            "kind IN ('deposit', 'withdrawal', 'transfer')",
            name="ck_ledger_transactions_kind_valid",
        ),
    )

    op.create_table(
        "postings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_postings"),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["ledger_transactions.id"],
            name="fk_postings_transaction_id_ledger_transactions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "currency"],
            ["accounts.id", "accounts.currency"],
            name="fk_postings_account_id_currency_accounts",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("amount_minor <> 0", name="ck_postings_amount_not_zero"),
    )
    op.create_index("ix_postings_transaction_id", "postings", ["transaction_id"])
    op.create_index("ix_postings_account_id_id", "postings", ["account_id", "id"])

    op.create_table(
        "idempotency_keys",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("user_id", "key", name="pk_idempotency_keys"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_idempotency_keys_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"])

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
    )
    op.create_index(
        "ix_outbox_events_pending",
        "outbox_events",
        ["next_attempt_at"],
        postgresql_where=sa.text("published_at IS NULL AND dead_lettered_at IS NULL"),
    )

    op.execute(APPEND_ONLY_FUNCTION)
    for table in ("ledger_transactions", "postings"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_ledger_mutation()"
        )

    op.execute(BALANCED_FUNCTION)
    op.execute(
        "CREATE CONSTRAINT TRIGGER postings_balanced AFTER INSERT ON postings "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION check_transaction_balanced()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS postings_balanced ON postings")
    op.execute("DROP FUNCTION IF EXISTS check_transaction_balanced()")
    for table in ("ledger_transactions", "postings"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS forbid_ledger_mutation()")

    op.drop_index("ix_outbox_events_pending", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_postings_account_id_id", table_name="postings")
    op.drop_index("ix_postings_transaction_id", table_name="postings")
    op.drop_table("postings")
    op.drop_table("ledger_transactions")
    op.drop_index("uq_accounts_system_code_currency", table_name="accounts")
    op.drop_index("ix_accounts_owner_id", table_name="accounts")
    op.drop_table("accounts")
    op.drop_table("users")
