"""CRUD + validation for per-user model API credentials."""

from __future__ import annotations

import hashlib
import logging
import os
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
from app.services.model_provider_endpoints import (
    OLLAMA_CLOUD_API_BASE,
    OLLAMA_LOCAL_DEFAULT_API_BASE,
    normalize_custom_base_url,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = frozenset(
    {"openai", "anthropic", "openrouter", "ollama", "ollama_local", "custom_openai"}
)

# Cheap auth probe per provider (must actually exercise credentials).
_OPENAI_VALIDATE_URL = "https://api.openai.com/v1/models"
_ANTHROPIC_VALIDATE_URL = "https://api.anthropic.com/v1/models"
_OPENROUTER_VALIDATE_URL = "https://openrouter.ai/api/v1/auth/key"
_OLLAMA_VALIDATE_URL = f"{OLLAMA_CLOUD_API_BASE}/api/chat"
_OLLAMA_LOCAL_DEFAULT_BASE_URL = OLLAMA_LOCAL_DEFAULT_API_BASE
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
        "base_url": getattr(record, "base_url", "") or None,
        "speech_provider": record.speech_provider or None,
        "speech_model": record.speech_model or None,
        "key_fingerprint": record.key_fingerprint,
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
    if provider_slug == "ollama_local":
        if key:
            raise ValueError(f"{label}_api_key must be empty for local Ollama")
        return "", ""
    if provider_slug != default_slug:
        if not key:
            raise ValueError(
                f"{label}_api_key is required when {label}_provider "
                "differs from provider"
            )
        valid, message = await validate_provider_api_key(provider_slug, key)
        if not valid:
            raise ValueError(f"{label} provider: {message}")
        if not encryption_available():
            raise RuntimeError(
                encryption_unavailable_reason()
                or "INTEGRAL_CREDENTIAL_ENC_KEY is not configured"
            )
        return (
            encrypt_secret_for_storage(key, aad=user_id),
            compute_key_fingerprint(key),
        )
    if key:
        valid, message = await validate_provider_api_key(provider_slug, key)
        if not valid:
            raise ValueError(f"{label} provider: {message}")
        if not encryption_available():
            raise RuntimeError(
                encryption_unavailable_reason()
                or "INTEGRAL_CREDENTIAL_ENC_KEY is not configured"
            )
        return (
            encrypt_secret_for_storage(key, aad=user_id),
            compute_key_fingerprint(key),
        )
    return "", ""


async def validate_provider_api_key(
    provider: str, api_key: Optional[str], base_url: Optional[str] = None
) -> tuple[bool, str]:
    """Probe provider access before persisting a key or local model choice."""
    slug = (provider or "").strip().lower()
    if slug not in SUPPORTED_PROVIDERS:
        return False, f"Unsupported provider: {provider}"
    if slug == "custom_openai":
        from app.services.custom_model_transport import probe_custom_model_endpoint

        try:
            base = normalize_custom_base_url(base_url)
        except ValueError as exc:
            return False, str(exc)
        return await probe_custom_model_endpoint(base, (api_key or "").strip())
    key = (api_key or "").strip()
    if slug == "ollama_local":
        if key:
            return False, "Local Ollama does not use an API key"
        base_url = (
            os.getenv("OLLAMA_API_BASE", _OLLAMA_LOCAL_DEFAULT_BASE_URL).strip()
            or _OLLAMA_LOCAL_DEFAULT_BASE_URL
        ).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base_url}/api/tags")
            if resp.status_code == 200:
                return True, "local Ollama reachable"
            return False, f"local Ollama returned HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            logger.warning("validate local Ollama failed: %s", type(exc).__name__)
            return False, "could not reach local Ollama"
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
    # Object.find() auto-creates declared indexes before reading. On a legacy
    # database with duplicate rows, that attempts the unique index too early
    # and prevents this migration from running.
    from jvspatial.core.context import get_default_context

    rows = await get_default_context().find(UserModelCredential, {})
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
    base_url: Optional[str] = None,
    clear_api_key: bool = False,
    speech_model: Optional[str] = None,
    speech_provider: Optional[str] = None,
    speech_api_key: Optional[str] = None,
    generation_settings: Optional[Dict[str, Any]] = None,
) -> UserModelCredential:
    """Validate + (re)persist a user's primary-model and voice-input credentials."""
    if settings.INTEGRAL_AGENT_KEY_MODE == "platform_only":
        raise RuntimeError("BYOK is disabled on this deployment")

    slug = provider.strip().lower()
    if slug not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")

    if clear_api_key and (slug != "custom_openai" or (api_key or "").strip()):
        raise ValueError("clear_api_key is only supported for keyless custom endpoints")
    from app.services.model_settings import validate_model_settings

    local_ollama = slug == "ollama_local"
    existing = await get_credential_for_user(user_id)
    configured_settings = validate_model_settings(
        slug,
        model.strip(),
        (
            generation_settings
            if generation_settings is not None
            else (
                getattr(existing, "generation_settings", {})
                if existing
                and existing.provider == slug
                and existing.model == model.strip()
                else {}
            )
        ),
    )
    # Encryption is required only when a secret is stored or retained. Keyless
    # custom endpoints persist no secret, so they work without vault config
    # (same exemption local Ollama enjoys).
    stores_secret = bool((api_key or "").strip() or (speech_api_key or "").strip())
    retains_secret = bool(existing and existing.api_key_enc and not clear_api_key)
    if (
        not local_ollama
        and not encryption_available()
        and (stores_secret or retains_secret)
    ):
        # Hosted credentials need a dedicated key at rest. Local Ollama stores
        # no secret, so that model choice does not require an encryption key.
        raise RuntimeError(
            encryption_unavailable_reason()
            or "INTEGRAL_CREDENTIAL_ENC_KEY is not configured"
        )

    custom = slug == "custom_openai"
    if custom:
        incoming_base = (base_url or "").strip()
        if not incoming_base and existing and existing.provider == "custom_openai":
            incoming_base = (getattr(existing, "base_url", "") or "").strip()
        base_normalized = normalize_custom_base_url(incoming_base)
    else:
        if (base_url or "").strip():
            raise ValueError("base_url is only supported for custom_openai")
        base_normalized = ""
    api_key_plain = (api_key or "").strip()
    if local_ollama and api_key_plain:
        raise ValueError("Local Ollama does not use an API key")
    if not api_key_plain and not local_ollama and not clear_api_key:
        same_endpoint = bool(existing and existing.provider == slug)
        if custom and same_endpoint:
            same_endpoint = (
                normalize_custom_base_url(existing.base_url) == base_normalized
            )
        if existing and not same_endpoint:
            raise ValueError(
                "Provider or endpoint changed: supply a new API key or explicitly choose no API key"
            )
        if existing:
            api_key_plain = decrypt_credential_api_key(existing)
            if existing.api_key_enc and not api_key_plain:
                raise ValueError(
                    "Stored API key could not be decrypted; replace or explicitly clear it"
                )
        if not api_key_plain and not custom:
            raise ValueError("api_key is required")
    if api_key_plain and len(api_key_plain) < 8:
        raise ValueError("api_key must be at least 8 characters")

    if custom:
        valid, message = await validate_provider_api_key(
            slug, api_key_plain, base_normalized or None
        )
    else:
        valid, message = await validate_provider_api_key(slug, api_key_plain)
    if not valid:
        raise ValueError(message)

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
        if provider_slug == "ollama_local":
            return await _validate_optional_slot_key(
                label=label,
                provider_slug=provider_slug,
                default_slug=slug,
                api_key_plain=incoming,
                user_id=user_id,
            )
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

    if speech_model_clean:
        speech_encrypted, speech_fingerprint = await _resolve_slot_key(
            label="speech",
            provider_slug=speech_slug,
            incoming_key=speech_api_key,
            existing_enc=(
                existing.speech_api_key_enc
                if existing
                and (existing.speech_provider or existing.provider) == speech_slug
                else ""
            ),
            existing_fingerprint=(
                existing.speech_key_fingerprint
                if existing
                and (existing.speech_provider or existing.provider) == speech_slug
                else ""
            ),
        )
    else:
        speech_slug, speech_encrypted, speech_fingerprint = slug, "", ""

    now = utc_now_iso()
    fingerprint = compute_key_fingerprint(api_key_plain)
    encrypted = (
        encrypt_secret_for_storage(api_key_plain, aad=user_id) if api_key_plain else ""
    )
    if existing:
        if (
            existing.provider != slug
            or existing.base_url != base_normalized
            or bool((api_key or "").strip())
            or clear_api_key
            or existing.model != model.strip()
            or existing.generation_settings != configured_settings
        ):
            existing.active_profile_id = ""
            existing.routing_mode = "single"
            existing.light_profile_id = ""
        existing.provider = slug
        existing.model = model.strip()
        existing.generation_settings = configured_settings
        existing.base_url = base_normalized
        existing.speech_provider = speech_slug if speech_slug != slug else ""
        existing.speech_model = speech_model_clean
        existing.api_key_enc = encrypted
        existing.key_fingerprint = fingerprint
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
        generation_settings=configured_settings,
        base_url=base_normalized,
        speech_provider=speech_slug if speech_slug != slug else "",
        speech_model=speech_model_clean,
        api_key_enc=encrypted,
        key_fingerprint=fingerprint,
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
    from datetime import datetime, timezone

    from jvspatial.core.context import get_default_context

    now = utc_now_iso()
    if record.last_used_at:
        try:
            previous = datetime.fromisoformat(
                record.last_used_at.replace("Z", "+00:00")
            )
            if (datetime.now(timezone.utc) - previous).total_seconds() < 60:
                return
        except (ValueError, TypeError):
            pass
    # Field-only telemetry: saving a hydrated credential here can overwrite a
    # concurrent key rotation or persist a conversation's transient model route.
    await get_default_context().database.find_one_and_update(
        "object",
        {"id": record.id, "context.user_id": record.user_id},
        {"$set": {"context.last_used_at": now}},
    )
    record.last_used_at = now


async def delete_credentials_for_user(user_id: str) -> int:
    """Hard-delete all credential rows for ``user_id``; return the count removed."""
    from jvspatial.core.context import get_default_context

    from app.models.credentials import UserModelConnection, UserModelProfile

    context = get_default_context()
    count = 0
    for cls in (UserModelProfile, UserModelConnection, UserModelCredential):
        await context.ensure_indexes(cls)
        while True:
            rows = await context.find(cls, {"context.user_id": user_id}, limit=100)
            if not rows:
                break
            for row in rows:
                await row.delete()
                count += 1
    return count
