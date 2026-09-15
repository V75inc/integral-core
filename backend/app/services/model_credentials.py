"""CRUD + validation for per-user model API credentials."""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.models.credentials import UserModelCredential
from app.schemas.model_credentials import SPEECH_CAPABLE_PROVIDERS
from app.services.credential_crypto import (
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
    encryption_available,
    encryption_unavailable_reason,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = frozenset({"openai", "anthropic", "openrouter", "ollama"})

# Cheap auth probe per provider (must actually exercise credentials).
_OPENAI_VALIDATE_URL = "https://api.openai.com/v1/models"
_ANTHROPIC_VALIDATE_URL = "https://api.anthropic.com/v1/models"
_OPENROUTER_VALIDATE_URL = "https://openrouter.ai/api/v1/auth/key"
_OLLAMA_VALIDATE_URL = "https://ollama.com/api/chat"
# Cloud model likely available on ollama.com; used only for key validation.
_OLLAMA_VALIDATE_MODEL = "gpt-oss:120b"


def _validate_headers(slug: str, api_key: str) -> Dict[str, str]:
    key = api_key.strip()
    if slug == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


def _interpret_auth_status(status_code: int, *, slug: str) -> tuple[bool, str]:
    if status_code == 200:
        return True, "validated"
    if status_code in (401, 403):
        return False, "invalid API key"
    # Ollama/OpenRouter: authenticated but model missing still proves the key.
    if slug == "ollama" and status_code == 404:
        return True, "validated"
    return False, f"provider returned HTTP {status_code}"


def compute_key_fingerprint(api_key: str) -> str:
    """Return a display-safe fingerprint (last4 + hash prefix)."""
    key = (api_key or "").strip()
    if not key:
        return ""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    last4 = key[-4:] if len(key) >= 4 else key
    return f"{digest}...{last4}"


def audit_snapshot_credential(record: UserModelCredential) -> Dict[str, Any]:
    """Metadata-only snapshot safe for ChangeEvent logging."""
    return {
        "provider": record.provider,
        "model": record.model,
        "light_provider": record.light_provider or None,
        "light_model": record.light_model or None,
        "heavy_provider": record.heavy_provider or None,
        "heavy_model": record.heavy_model or None,
        "vision_provider": record.vision_provider or None,
        "vision_model": record.vision_model or None,
        "speech_provider": record.speech_provider or None,
        "speech_model": record.speech_model or None,
        "key_fingerprint": record.key_fingerprint,
        "light_key_fingerprint": record.light_key_fingerprint or None,
        "heavy_key_fingerprint": record.heavy_key_fingerprint or None,
        "vision_key_fingerprint": record.vision_key_fingerprint or None,
        "speech_key_fingerprint": record.speech_key_fingerprint or None,
        "is_active": bool(record.is_active),
        "validated_at": record.validated_at,
    }


async def _validate_optional_slot_key(
    *,
    label: str,
    provider_slug: str,
    default_slug: str,
    api_key_plain: str,
    user_id: str,
) -> tuple[str, str]:
    """Return (encrypted, fingerprint) for an optional slot key."""
    key = (api_key_plain or "").strip()
    if provider_slug != default_slug:
        if not key:
            raise ValueError(
                f"{label}_api_key is required when {label}_provider "
                "differs from provider"
            )
        valid, message = await validate_provider_api_key(provider_slug, key)
        if not valid:
            raise ValueError(f"{label} provider: {message}")
        return (
            encrypt_secret_for_storage(key, aad=user_id),
            compute_key_fingerprint(key),
        )
    if key:
        valid, message = await validate_provider_api_key(provider_slug, key)
        if not valid:
            raise ValueError(f"{label} provider: {message}")
        return (
            encrypt_secret_for_storage(key, aad=user_id),
            compute_key_fingerprint(key),
        )
    return "", ""


async def validate_provider_api_key(provider: str, api_key: str) -> tuple[bool, str]:
    """Cheap provider ping before persisting a user key."""
    slug = (provider or "").strip().lower()
    if slug not in SUPPORTED_PROVIDERS:
        return False, f"Unsupported provider: {provider}"
    key = (api_key or "").strip()
    if not key:
        return False, "invalid API key"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if slug == "ollama":
                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                }
                resp = await client.post(
                    _OLLAMA_VALIDATE_URL,
                    headers=headers,
                    json={
                        "model": _OLLAMA_VALIDATE_MODEL,
                        "messages": [{"role": "user", "content": "ping"}],
                        "stream": False,
                    },
                )
            elif slug == "openrouter":
                resp = await client.get(
                    _OPENROUTER_VALIDATE_URL,
                    headers=_validate_headers(slug, key),
                )
            elif slug == "anthropic":
                resp = await client.get(
                    _ANTHROPIC_VALIDATE_URL,
                    headers=_validate_headers(slug, key),
                )
            else:
                resp = await client.get(
                    _OPENAI_VALIDATE_URL,
                    headers=_validate_headers(slug, key),
                )
        if resp.status_code not in (200, 401, 403, 404):
            logger.warning(
                "validate_provider_api_key %s HTTP %s: %s",
                slug,
                resp.status_code,
                (resp.text or "")[:200],
            )
        return _interpret_auth_status(resp.status_code, slug=slug)
    except httpx.HTTPError as exc:
        logger.warning("validate_provider_api_key failed: %s", type(exc).__name__)
        return False, "could not reach provider"


async def get_active_credential_for_user(user_id: str) -> Optional[UserModelCredential]:
    """Return the active BYOK credential record for ``user_id``, or None."""
    if not user_id:
        return None
    rows = await UserModelCredential.find(
        {"context.user_id": user_id, "context.is_active": True},
    )
    return rows[0] if rows else None


def _pick_canonical_credential(
    rows: list[UserModelCredential],
) -> UserModelCredential:
    """Choose the row to keep when duplicate ``user_id`` rows exist."""

    def _sort_key(record: UserModelCredential) -> tuple[int, str]:
        active_rank = 1 if record.is_active else 0
        stamp = record.updated_at or record.created_at or ""
        return active_rank, stamp

    return max(rows, key=_sort_key)


async def get_credential_for_user(user_id: str) -> Optional[UserModelCredential]:
    """Return the canonical credential row for ``user_id`` (active or revoked)."""
    if not user_id:
        return None
    rows = await UserModelCredential.find({"context.user_id": user_id})
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    return _pick_canonical_credential(rows)


async def dedupe_user_model_credentials() -> int:
    """Remove duplicate credential rows per user so the unique index can apply."""
    rows = await UserModelCredential.find({})
    by_user: dict[str, list[UserModelCredential]] = {}
    for row in rows:
        uid = (row.user_id or "").strip()
        if not uid:
            continue
        by_user.setdefault(uid, []).append(row)

    removed = 0
    for group in by_user.values():
        if len(group) <= 1:
            continue
        keep = _pick_canonical_credential(group)
        for row in group:
            if row.id != keep.id:
                await row.delete()
                removed += 1
    return removed


async def upsert_user_credential(
    *,
    user_id: str,
    provider: str,
    model: str,
    api_key: Optional[str] = None,
    light_model: Optional[str] = None,
    light_provider: Optional[str] = None,
    light_api_key: Optional[str] = None,
    heavy_model: Optional[str] = None,
    heavy_provider: Optional[str] = None,
    heavy_api_key: Optional[str] = None,
    vision_model: Optional[str] = None,
    vision_provider: Optional[str] = None,
    vision_api_key: Optional[str] = None,
    speech_model: Optional[str] = None,
    speech_provider: Optional[str] = None,
    speech_api_key: Optional[str] = None,
) -> UserModelCredential:
    """Validate + (re)persist a user's BYOK credential (default + optional slots)."""
    if settings.INTEGRAL_AGENT_KEY_MODE == "platform_only":
        raise RuntimeError("BYOK is disabled on this deployment")
    if not encryption_available():
        # Report WHICH failure it is: "set but invalid" and "not set" send an
        # operator to different fixes, and the old flat message claimed the
        # variable was missing even when it was plainly present in their .env.
        raise RuntimeError(
            encryption_unavailable_reason()
            or "INTEGRAL_CREDENTIAL_ENC_KEY is not configured"
        )

    slug = provider.strip().lower()
    if slug not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")

    existing = await get_credential_for_user(user_id)
    api_key_plain = (api_key or "").strip()
    if not api_key_plain:
        if not existing:
            raise ValueError("api_key is required")
        api_key_plain = decrypt_credential_api_key(existing)
    elif len(api_key_plain) < 8:
        raise ValueError("api_key must be at least 8 characters")

    valid, message = await validate_provider_api_key(slug, api_key_plain)
    if not valid:
        raise ValueError(message)

    light_slug = (light_provider or "").strip().lower() or slug
    if light_slug not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported light provider: {light_provider}")

    heavy_slug = (heavy_provider or "").strip().lower() or slug
    if heavy_slug not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported heavy provider: {heavy_provider}")

    vision_slug = (vision_provider or "").strip().lower() or slug
    if vision_slug not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported vision provider: {vision_provider}")

    # The speech slot is opt-in: no model means the slot is off, and any stored
    # slot key is dropped rather than left orphaned behind an empty provider.
    speech_model_clean = (speech_model or "").strip()
    speech_slug = (speech_provider or "").strip().lower() or slug
    if speech_model_clean and speech_slug not in SPEECH_CAPABLE_PROVIDERS:
        raise ValueError(f"Unsupported voice input provider: {speech_slug}")

    async def _resolve_slot_key(
        *,
        label: str,
        provider_slug: str,
        incoming_key: Optional[str],
        existing_enc: str,
        existing_fingerprint: str,
    ) -> tuple[str, str]:
        incoming = (incoming_key or "").strip()
        if incoming:
            return await _validate_optional_slot_key(
                label=label,
                provider_slug=provider_slug,
                default_slug=slug,
                api_key_plain=incoming,
                user_id=user_id,
            )
        if existing and (existing_enc or existing_fingerprint):
            return existing_enc or "", existing_fingerprint or ""
        return await _validate_optional_slot_key(
            label=label,
            provider_slug=provider_slug,
            default_slug=slug,
            api_key_plain="",
            user_id=user_id,
        )

    light_encrypted, light_fingerprint = await _resolve_slot_key(
        label="light",
        provider_slug=light_slug,
        incoming_key=light_api_key,
        existing_enc=existing.light_api_key_enc if existing else "",
        existing_fingerprint=existing.light_key_fingerprint if existing else "",
    )
    heavy_encrypted, heavy_fingerprint = await _resolve_slot_key(
        label="heavy",
        provider_slug=heavy_slug,
        incoming_key=heavy_api_key,
        existing_enc=existing.heavy_api_key_enc if existing else "",
        existing_fingerprint=existing.heavy_key_fingerprint if existing else "",
    )
    vision_encrypted, vision_fingerprint = await _resolve_slot_key(
        label="vision",
        provider_slug=vision_slug,
        incoming_key=vision_api_key,
        existing_enc=existing.vision_api_key_enc if existing else "",
        existing_fingerprint=existing.vision_key_fingerprint if existing else "",
    )
    if speech_model_clean:
        speech_encrypted, speech_fingerprint = await _resolve_slot_key(
            label="speech",
            provider_slug=speech_slug,
            incoming_key=speech_api_key,
            existing_enc=existing.speech_api_key_enc if existing else "",
            existing_fingerprint=existing.speech_key_fingerprint if existing else "",
        )
    else:
        speech_slug, speech_encrypted, speech_fingerprint = slug, "", ""

    now = utc_now_iso()
    fingerprint = compute_key_fingerprint(api_key_plain)
    encrypted = encrypt_secret_for_storage(api_key_plain, aad=user_id)
    if existing:
        existing.provider = slug
        existing.model = model.strip()
        existing.light_provider = light_slug if light_slug != slug else ""
        existing.light_model = (light_model or "").strip()
        existing.heavy_provider = heavy_slug if heavy_slug != slug else ""
        existing.heavy_model = (heavy_model or "").strip()
        existing.vision_provider = vision_slug if vision_slug != slug else ""
        existing.vision_model = (vision_model or "").strip()
        existing.speech_provider = speech_slug if speech_slug != slug else ""
        existing.speech_model = speech_model_clean
        existing.api_key_enc = encrypted
        existing.key_fingerprint = fingerprint
        existing.light_api_key_enc = light_encrypted
        existing.light_key_fingerprint = light_fingerprint
        existing.heavy_api_key_enc = heavy_encrypted
        existing.heavy_key_fingerprint = heavy_fingerprint
        existing.vision_api_key_enc = vision_encrypted
        existing.vision_key_fingerprint = vision_fingerprint
        existing.speech_api_key_enc = speech_encrypted
        existing.speech_key_fingerprint = speech_fingerprint
        existing.is_active = True
        existing.validated_at = now
        existing.updated_at = now
        await existing.save()
        return existing

    record = UserModelCredential(
        user_id=user_id,
        provider=slug,
        model=model.strip(),
        light_provider=light_slug if light_slug != slug else "",
        light_model=(light_model or "").strip(),
        heavy_provider=heavy_slug if heavy_slug != slug else "",
        heavy_model=(heavy_model or "").strip(),
        vision_provider=vision_slug if vision_slug != slug else "",
        vision_model=(vision_model or "").strip(),
        speech_provider=speech_slug if speech_slug != slug else "",
        speech_model=speech_model_clean,
        api_key_enc=encrypted,
        key_fingerprint=fingerprint,
        light_api_key_enc=light_encrypted,
        light_key_fingerprint=light_fingerprint,
        heavy_api_key_enc=heavy_encrypted,
        heavy_key_fingerprint=heavy_fingerprint,
        vision_api_key_enc=vision_encrypted,
        vision_key_fingerprint=vision_fingerprint,
        speech_api_key_enc=speech_encrypted,
        speech_key_fingerprint=speech_fingerprint,
        is_active=True,
        validated_at=now,
        created_at=now,
        updated_at=now,
    )
    await record.save()
    return record


async def revoke_user_credential(user_id: str) -> bool:
    """Deactivate the user's active BYOK credential; return False if none existed."""
    record = await get_active_credential_for_user(user_id)
    if not record:
        return False
    record.is_active = False
    record.updated_at = utc_now_iso()
    await record.save()
    return True


def decrypt_credential_api_key(record: UserModelCredential) -> str:
    """Decrypt the default-slot API key (AAD-bound to the record's user_id)."""
    return decrypt_secret_from_storage(record.api_key_enc or "", aad=record.user_id)


def decrypt_credential_light_api_key(record: UserModelCredential) -> str:
    """Decrypt the light-slot key, falling back to the default-slot key."""
    if record.light_api_key_enc:
        return decrypt_secret_from_storage(record.light_api_key_enc, aad=record.user_id)
    return decrypt_credential_api_key(record)


def decrypt_credential_heavy_api_key(record: UserModelCredential) -> str:
    """Decrypt the heavy-slot key, falling back to the default-slot key."""
    if record.heavy_api_key_enc:
        return decrypt_secret_from_storage(record.heavy_api_key_enc, aad=record.user_id)
    return decrypt_credential_api_key(record)


def decrypt_credential_vision_api_key(record: UserModelCredential) -> str:
    """Decrypt the vision-slot key, falling back to the default-slot key."""
    if record.vision_api_key_enc:
        return decrypt_secret_from_storage(
            record.vision_api_key_enc, aad=record.user_id
        )
    return decrypt_credential_api_key(record)


def decrypt_credential_speech_api_key(record: UserModelCredential) -> str:
    """Decrypt the voice-input key, falling back to the default-slot key.

    A separate key is stored only when the speech provider differs from the
    default provider, so an empty slot means "reuse the primary key".
    """
    if record.speech_api_key_enc:
        return decrypt_secret_from_storage(
            record.speech_api_key_enc, aad=record.user_id
        )
    return decrypt_credential_api_key(record)


async def touch_credential_last_used(record: UserModelCredential) -> None:
    """Stamp ``last_used_at`` on the credential (best-effort usage marker)."""
    record.last_used_at = utc_now_iso()
    await record.save()


async def delete_credentials_for_user(user_id: str) -> int:
    """Hard-delete all credential rows for ``user_id``; return the count removed."""
    rows = await UserModelCredential.find({"context.user_id": user_id})
    count = 0
    for row in rows:
        await row.delete()
        count += 1
    return count
