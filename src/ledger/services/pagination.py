"""Opaque cursors: clients pass them back unchanged and never depend on their contents."""

from __future__ import annotations

import base64
import binascii

from ledger.domain.errors import InvalidCursor

_PREFIX = "posting:"


def encode_cursor(posting_id: int) -> str:
    return base64.urlsafe_b64encode(f"{_PREFIX}{posting_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise InvalidCursor() from None
    if not raw.startswith(_PREFIX) or not raw.removeprefix(_PREFIX).isdigit():
        raise InvalidCursor()
    return int(raw.removeprefix(_PREFIX))
