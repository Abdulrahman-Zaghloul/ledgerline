from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledger.db.models import OutboxEvent


class OutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, *, event_type: str, aggregate_id: uuid.UUID, payload: dict[str, Any]) -> None:
        self._session.add(
            OutboxEvent(event_type=event_type, aggregate_id=aggregate_id, payload=payload)
        )

    def claim_due(self, limit: int) -> list[OutboxEvent]:
        """Lock up to ``limit`` due events. SKIP LOCKED lets many relay workers run at once
        without ever grabbing the same event."""
        stmt = (
            select(OutboxEvent)
            .where(
                OutboxEvent.published_at.is_(None),
                OutboxEvent.dead_lettered_at.is_(None),
                OutboxEvent.next_attempt_at <= func.now(),
            )
            .order_by(OutboxEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(self._session.scalars(stmt))
