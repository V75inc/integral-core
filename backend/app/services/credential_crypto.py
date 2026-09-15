"""AES-256-GCM encryption for user model API keys at rest."""

from __future__ import annotations

import base64
import logging
import os
import secrets
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

logger = logging.getLogger(__name__)

CIPHER_PREFIX_V1 = "v1:"
_NONCE_BYTES = 12
_HKDF_INFO = b"integral-model-credential-encryption-v1"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _derive_key_from_jwt(jwt_secret: str) -> bytes:
    h = hmac.HMAC(b"integral-cred-enc", hashes.SHA256())
    h.update(jwt_secret.encode("utf-8"))
    prk = h.finalize()
    e = hmac.HMAC(prk, hashes.SHA256())
    e.update(_HKDF_INFO + b"\x01")
    return e.finalize()


def _normalize_key_bytes(raw: str) -> Optional[bytes]:
    if not raw:
        return None
    raw = raw.strip()
    for decoder in (_b64url_decode, base64.b64decode):
        try:
            candidate = decoder(raw)
            if len(candidate) == 32:
                return candidate
        except Exception:
            pass
    try:
        candidate = bytes.fromhex(raw)
        if len(candidate) == 32:
            return candidate
    except Exception:
        pass
    encoded = raw.encode("utf-8")
    if len(encoded) == 32:
        return encoded
    return None


_SET_BUT_INVALID = (
    "INTEGRAL_CREDENTIAL_ENC_KEY is set but is not a valid 32-byte key. "
    "Accepted forms: base64 or base64url (44 chars, as produced by "
    "`openssl rand -base64 32`), 64-character hex, or 32 raw bytes. "
    "Check for a truncated paste, surrounding quotes, or a trailing comment."
)
_UNSET = (
    "INTEGRAL_CREDENTIAL_ENC_KEY is not set. Generate one with "
    "`openssl rand -base64 32` and add it to the repo-root .env, or set "
    "DEBUG=true to derive a development key from SECRET_KEY."
)
_UNSET_NO_SECRET = (
    "INTEGRAL_CREDENTIAL_ENC_KEY is not set, and no SECRET_KEY is available to "
    "derive a development key from. Generate one with `openssl rand -base64 32`."
)


def _resolve_key() -> tuple[Optional[bytes], Optional[str]]:
    """Return ``(key, reason_unavailable)``.

    The two failure modes are reported separately on purpose. "Set but not a
    valid 32-byte key" and "not set at all" send an operator to completely
    different places, and reporting the first as "not configured" is actively
    misleading — the variable IS in their .env, plainly visible, so they go
    looking for an env-loading bug that does not exist.
    """
    explicit = os.environ.get("INTEGRAL_CREDENTIAL_ENC_KEY", "").strip()
    if not explicit and settings.INTEGRAL_CREDENTIAL_ENC_KEY:
        explicit = settings.INTEGRAL_CREDENTIAL_ENC_KEY.strip()
    if explicit:
        key = _normalize_key_bytes(explicit)
        if key is None:
            logger.warning(
                "INTEGRAL_CREDENTIAL_ENC_KEY set but did not decode to 32 bytes "
                "(got %d characters)",
                len(explicit),
            )
            return None, _SET_BUT_INVALID
        return key, None
    if settings.DEBUG:
        # Full Sweep S7: still allow local DEBUG derivation, but only when
        # an explicit opt-in is set so shared DEBUG staging envs cannot
        # silently couple ciphertext to SECRET_KEY.
        allow_derive = os.environ.get(
            "INTEGRAL_CREDENTIAL_ALLOW_DEBUG_DERIVE", ""
        ).strip().lower() in ("1", "true", "yes")
        if allow_derive:
            jwt = (settings.SECRET_KEY or "").strip()
            if jwt:
                logger.warning(
                    "INTEGRAL_CREDENTIAL_ENC_KEY unset — deriving dev key from JWT secret "
                    "(INTEGRAL_CREDENTIAL_ALLOW_DEBUG_DERIVE=1)"
                )
                return _derive_key_from_jwt(jwt), None
        return None, _UNSET_NO_SECRET
    return None, _UNSET


def _current_key() -> Optional[bytes]:
    return _resolve_key()[0]


def encryption_unavailable_reason() -> Optional[str]:
    """Why credential encryption is unavailable, or ``None`` when it works."""
    return _resolve_key()[1]


def _previous_key() -> Optional[bytes]:
    prev = os.environ.get("INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS", "").strip()
    if not prev and settings.INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS:
        prev = settings.INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS.strip()
    if not prev:
        return None
    return _normalize_key_bytes(prev)


def encryption_available() -> bool:
    """Return True when a credential-encryption key is configured (or derivable in dev)."""
    return _current_key() is not None


def encrypt_secret_for_storage(plaintext: str, *, aad: Optional[str] = None) -> str:
    """Encrypt ``plaintext`` for at-rest storage (AES-256-GCM, ``v1:`` prefix).

    ``aad`` (e.g. the owning ``user_id``) is bound as GCM associated data so a
    ciphertext cannot be replayed under a different row. The same ``aad`` MUST be
    supplied to :func:`decrypt_secret_from_storage`. Empty input is returned as-is.
    """
    if not plaintext:
        return plaintext
    key, reason = _resolve_key()
    if key is None:
        raise RuntimeError(reason or "INTEGRAL_CREDENTIAL_ENC_KEY is required")
    aad_bytes = aad.encode("utf-8") if aad else None
    nonce = secrets.token_bytes(_NONCE_BYTES)
    ct_with_tag = AESGCM(key).encrypt(
        nonce, plaintext.encode("utf-8"), associated_data=aad_bytes
    )
    return CIPHER_PREFIX_V1 + _b64url_encode(nonce + ct_with_tag)


def decrypt_secret_from_storage(stored: str, *, aad: Optional[str] = None) -> str:
    """Decrypt an at-rest secret produced by :func:`encrypt_secret_for_storage`.

    ``aad`` must match the value bound at encrypt time. Returns ``""`` on any
    decryption failure (logged), or ``stored`` unchanged if it is not a ``v1:``
    ciphertext (plaintext-passthrough for un-migrated values).
    """
    if not stored:
        return stored
    if not stored.startswith(CIPHER_PREFIX_V1):
        return stored
    body = stored[len(CIPHER_PREFIX_V1) :]
    try:
        blob = _b64url_decode(body)
    except Exception:
        logger.warning("decrypt_secret_from_storage: malformed base64 payload")
        return ""
    if len(blob) <= _NONCE_BYTES:
        logger.warning("decrypt_secret_from_storage: payload too short")
        return ""
    aad_bytes = aad.encode("utf-8") if aad else None
    nonce, ct_with_tag = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    for key in (_current_key(), _previous_key()):
        if key is None:
            continue
        try:
            return (
                AESGCM(key)
                .decrypt(nonce, ct_with_tag, associated_data=aad_bytes)
                .decode("utf-8")
            )
        except InvalidTag:
            continue
        except Exception as exc:
            logger.warning("decrypt_secret_from_storage failed: %s", type(exc).__name__)
    logger.warning("decrypt_secret_from_storage: no key decrypted the payload")
    return ""
