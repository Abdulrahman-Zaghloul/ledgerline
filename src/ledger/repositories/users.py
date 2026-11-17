from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledger.db.models import User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, user: User) -> User:
        self._session.add(user)
        self._session.flush()
        return user

    def get(self, user_id: uuid.UUID) -> User | None:
        return self._session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        return self._session.scalars(select(User).where(User.email == email)).one_or_none()
