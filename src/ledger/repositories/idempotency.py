from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ledger.db.models import IdempotencyKey


class IdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim(self, user_id: uuid.UUID, key: str, fingerprint: str) -> bool:
        """Try to reserve a key. Returns False if it already exists.

        If another transaction is inserting the same key right now, PostgreSQL makes
        this INSERT wait until that transaction commits or rolls back, which is
        what serialises concurrent retries of the same request.
        """
        stmt = (
            insert(IdempotencyKey)
            .values(user_id=user_id, key=key, request_fingerprint=fingerprint)
            .on_conflict_do_nothing(index_elements=["user_id", "key"])
            .returning(IdempotencyKey.key)
        )
        return self._session.execute(stmt).first() is not None

    def get(self, user_id: uuid.UUID, key: str) -> IdempotencyKey | None:
        stmt = (
            select(IdempotencyKey)
            .where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
            .execution_options(populate_existing=True)
        )
        return self._session.scalars(stmt).one_or_none()

    def record_response(
        self, user_id: uuid.UUID, key: str, status_code: int, body: dict[str, Any]
    ) -> None:
        stmt = (
            update(IdempotencyKey)
            .where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
            .values(response_status=status_code, response_body=body)
        )
        self._session.execute(stmt)

    def purge_created_before(self, cutoff: datetime) -> int:
        result = self._session.execute(
            delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]
