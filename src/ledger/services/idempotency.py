"""Safe retries for money-moving requests (ADR 0004).

The key, the money movement and the stored response are all written in ONE database
transaction. Either all of them are saved or none are, so there is no window in which
money moved but the response was lost (or the reverse).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ledger.domain.errors import IdempotencyKeyReused, LedgerError
from ledger.repositories.idempotency import IdempotencyRepository

Operation = Callable[[], tuple[int, dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class IdempotentResult:
    status_code: int
    body: dict[str, Any]
    replayed: bool


def fingerprint_request(method: str, path: str, payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"method": method.upper(), "path": path, "body": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def execute_idempotently(
    session: Session,
    *,
    user_id: uuid.UUID,
    key: str,
    fingerprint: str,
    operation: Operation,
) -> IdempotentResult:
    """Run ``operation`` at most once per (user, key) and commit.

    * New key: run the operation, store its response, commit everything together.
    * Known key, same request: return the stored response without running anything.
    * Known key, different request: refuse (the client has a bug).
    * Operation fails: roll back everything, including the key, so the client may retry.
    """
    keys = IdempotencyRepository(session)
    try:
        if not keys.claim(user_id, key, fingerprint):
            existing = keys.get(user_id, key)
            if existing is None or existing.response_status is None:
                raise LedgerError("Idempotency record vanished mid-request")
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyKeyReused()
            replay = IdempotentResult(
                existing.response_status, dict(existing.response_body or {}), replayed=True
            )
            session.rollback()  # nothing was written; release the transaction
            return replay

        status_code, body = operation()
        keys.record_response(user_id, key, status_code, body)
        session.commit()
        return IdempotentResult(status_code, body, replayed=False)
    except BaseException:
        session.rollback()
        raise
