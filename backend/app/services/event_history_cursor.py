"""Confidential, request-bound continuation for scans through withheld events.

This is a transient transport token, not a stored data encryption format or an
authorization grant. Signing-key rotation invalidates it; event permissions are
still evaluated against current facts on every read.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.api.errors import BadRequestError, QueryUnavailableError
from app.config import settings

PREFIX = "eh1:"


def _key() -> bytes:
    secret = settings.SECRET_KEY
    if not secret:
        raise QueryUnavailableError(message="Event continuation is unavailable")
    return hmac.new(
        secret.encode(), b"integral-event-history-cursor-v1", hashlib.sha256
    ).digest()


def _aad(binding: dict[str, Any]) -> bytes:
    return json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()


def encode_history_cursor(position: str, *, binding: dict[str, Any]) -> str:
    """Hide denied-row identities and authenticate the request's query binding."""
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(_key()).encrypt(nonce, position.encode(), _aad(binding))
    return PREFIX + base64.urlsafe_b64encode(nonce + encrypted).decode()


def decode_history_cursor(cursor: str, *, binding: dict[str, Any]) -> str:
    """Accept prior visible-row cursors; new private continuations must match."""
    if not cursor.startswith(PREFIX):
        return cursor
    key = _key()
    try:
        if len(cursor) > 4096:
            raise ValueError("Invalid cursor size")
        raw = base64.b64decode(cursor[len(PREFIX) :], altchars=b"-_", validate=True)
        return AESGCM(key).decrypt(raw[:12], raw[12:], _aad(binding)).decode()
    except Exception as exc:
        raise BadRequestError(message="Invalid event history cursor") from exc
