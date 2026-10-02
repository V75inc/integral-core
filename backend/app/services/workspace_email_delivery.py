"""Workspace-scoped transactional email delivery (Resend / SendGrid)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.middleware.agentive_scope import get_scope_key
from app.models.nodes import Workspace
from app.models.workspace_email_delivery import WorkspaceEmailDelivery
from app.services.credential_crypto import (
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
    encryption_available,
)
from app.services.email_source_kinds import SYSTEM_EMAIL_SOURCE_KINDS
from app.services.model_credentials import compute_key_fingerprint
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

_VALID_PROVIDERS = frozenset({"resend", "sendgrid"})


@dataclass
class SendContext:
    provider: str
    api_key: Optional[str]
    from_email: str
    from_name: str
    delivery_source: str  # workspace | platform


def _field_str(value: Any) -> str:
    return str(value or "").strip()


def _provider_key_mismatch_message(provider: str, api_key: str) -> Optional[str]:
    """Detect common Resend/SendGrid key pasted under the wrong provider."""
    slug = _field_str(provider).lower()
    key = _field_str(api_key)
    if not key:
        return None
    if slug == "sendgrid" and key.startswith("re_"):
        return (
            "This looks like a Resend API key (re_…). "
            "Set Provider to Resend, then save again."
        )
    if slug == "resend" and key.startswith("SG."):
        return (
            "This looks like a SendGrid API key (SG.…). "
            "Set Provider to SendGrid, then save again."
        )
    return None


def _resend_key_invalid_message(resp: httpx.Response) -> Optional[str]:
    if resp.status_code != 400:
        return None
    try:
        body = resp.json()
    except Exception:
        return None
    msg = str(body.get("message") or "").lower()
    if "invalid" in msg and "key" in msg:
        return "invalid Resend API key"
    return None


def _resend_sending_only_key(resp: httpx.Response) -> bool:
    """Resend sending-only keys cannot list domains/keys but can send mail."""
    if resp.status_code not in (401, 403):
        return False
    try:
        body = resp.json()
    except Exception:
        return False
    name = str(body.get("name") or "").lower()
    msg = str(body.get("message") or "").lower()
    if name == "restricted_api_key":
        return True
    return "restricted" in msg and "send" in msg


async def _get_record(workspace_id: str) -> Optional[WorkspaceEmailDelivery]:
    ws_id = _field_str(workspace_id)
    if not ws_id:
        return None
    # Object fields persist under ``context.*`` (see UserModelCredential / Entitlement).
    record = await WorkspaceEmailDelivery.find_one({"context.workspace_id": ws_id})
    if record is None:
        record = await WorkspaceEmailDelivery.find_one({"workspace_id": ws_id})
    return record


async def _is_org_workspace(workspace_id: str) -> bool:
    ws = await Workspace.get(workspace_id)
    if ws is None:
        return False
    kind = _field_str(getattr(ws, "kind", "") or "organization").lower()
    return kind != "personal"


def redacted_snapshot(record: Optional[WorkspaceEmailDelivery]) -> Dict[str, Any]:
    if record is None:
        return {
            "configured": False,
            "is_enabled": False,
            "has_api_key": False,
        }
    return {
        "configured": bool(record.api_key_enc),
        "is_enabled": bool(record.is_enabled),
        "provider": record.provider or "",
        "from_email": record.from_email or "",
        "from_name": record.from_name or "",
        "reply_to": record.reply_to or "",
        "has_api_key": bool(record.api_key_enc),
        "key_fingerprint": record.key_fingerprint or "",
        "validated_at": record.validated_at,
        "last_used_at": record.last_used_at,
        "last_error": record.last_error,
    }


async def get_redacted(workspace_id: str) -> Dict[str, Any]:
    return redacted_snapshot(await _get_record(workspace_id))


async def workspace_has_delivery_record(workspace_id: str) -> bool:
    return await _get_record(workspace_id) is not None


async def resolve_api_key_for_validation(
    workspace_id: str, payload: Dict[str, Any]
) -> tuple[str, str]:
    """Return (provider, api_key) for validate — reuse stored key when omitted."""
    provider = _field_str(
        (payload or {}).get("provider") or (payload or {}).get("delivery_provider")
    ).lower()
    api_key = _field_str((payload or {}).get("api_key"))
    if api_key:
        return provider, api_key
    record = await _get_record(workspace_id)
    if record is None or not record.api_key_enc:
        return provider, ""
    try:
        stored = decrypt_secret_from_storage(record.api_key_enc) or ""
    except Exception:
        return provider, ""
    return provider or (record.provider or ""), stored


async def validate_delivery_provider(provider: str, api_key: str) -> tuple[bool, str]:
    slug = _field_str(provider).lower()
    key = _field_str(api_key)
    if slug not in _VALID_PROVIDERS:
        return False, f"unsupported provider {slug!r}"
    if not key:
        return False, "API key is required"
    mismatch = _provider_key_mismatch_message(slug, key)
    if mismatch:
        return False, mismatch
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if slug == "sendgrid":
                resp = await client.get(
                    "https://api.sendgrid.com/v3/user/profile",
                    headers={"Authorization": f"Bearer {key}"},
                )
            else:
                resp = await client.get(
                    "https://api.resend.com/domains",
                    headers={"Authorization": f"Bearer {key}"},
                )
        if resp.status_code == 200:
            return True, "validated"
        if slug == "resend":
            resend_invalid = _resend_key_invalid_message(resp)
            if resend_invalid:
                return False, resend_invalid
            if _resend_sending_only_key(resp):
                return True, "validated (sending access)"
        if resp.status_code in (401, 403):
            return False, "invalid API key"
        return False, f"provider returned HTTP {resp.status_code}"
    except Exception as exc:
        logger.warning("validate_delivery_provider failed: %s", type(exc).__name__)
        return False, "could not reach provider"


async def upsert_delivery_config(
    *,
    workspace_id: str,
    actor_user_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Create or update workspace delivery. API key optional (keep existing when omitted)."""
    ws_id = _field_str(workspace_id)
    if not ws_id:
        return {"ok": False, "error": "workspace_id required"}
    if not await _is_org_workspace(ws_id):
        return {
            "ok": False,
            "error": "workspace delivery is only for organization workspaces",
        }

    provider = _field_str(
        payload.get("provider") or payload.get("delivery_provider")
    ).lower()
    from_email = _field_str(payload.get("from_email"))
    from_name = _field_str(payload.get("from_name"))
    reply_to = _field_str(payload.get("reply_to")) or None
    enabled_raw = payload.get("is_enabled", payload.get("delivery_enabled"))
    is_enabled = bool(enabled_raw) if enabled_raw is not None else True
    api_key_plain = _field_str(payload.get("api_key"))

    record = await _get_record(ws_id)
    now = utc_now_iso()
    if record is None:
        if provider not in _VALID_PROVIDERS:
            return {"ok": False, "error": "provider must be resend or sendgrid"}
        if not encryption_available():
            return {"ok": False, "error": "credential encryption unavailable"}
        if not api_key_plain:
            return {"ok": False, "error": "api_key required on first setup"}
        valid, msg = await validate_delivery_provider(provider, api_key_plain)
        if not valid:
            return {"ok": False, "error": msg}
        enc = encrypt_secret_for_storage(api_key_plain)
        record = WorkspaceEmailDelivery(
            workspace_id=ws_id,
            provider=provider,
            from_email=from_email,
            from_name=from_name,
            reply_to=reply_to,
            api_key_enc=enc,
            key_fingerprint=compute_key_fingerprint(api_key_plain),
            is_enabled=is_enabled,
            validated_at=now if valid else None,
            created_at=now,
            updated_at=now,
        )
        await record.save()
        return {"ok": True, **await get_redacted(ws_id)}

    if provider and provider in _VALID_PROVIDERS:
        record.provider = provider
    if from_email:
        record.from_email = from_email
    if from_name:
        record.from_name = from_name
    if "reply_to" in payload:
        record.reply_to = reply_to
    if enabled_raw is not None:
        record.is_enabled = is_enabled

    if api_key_plain:
        if not encryption_available():
            return {"ok": False, "error": "credential encryption unavailable"}
        validate_provider = (
            provider if provider in _VALID_PROVIDERS else _field_str(record.provider)
        )
        valid, msg = await validate_delivery_provider(
            validate_provider, api_key_plain
        )
        if not valid:
            record.last_error = msg[:500]
            record.updated_at = now
            await record.save()
            return {"ok": False, "error": msg}
        record.api_key_enc = encrypt_secret_for_storage(api_key_plain)
        record.key_fingerprint = compute_key_fingerprint(api_key_plain)
        record.validated_at = now
        record.last_error = None

    record.updated_at = now
    await record.save()
    return {"ok": True, **await get_redacted(ws_id)}


async def resolve_send_context(
    *,
    workspace_id: Optional[str],
    source_kind: Optional[str],
) -> SendContext:
    """Pick platform vs workspace credentials for one send."""
    platform_provider = (settings.EMAIL_PROVIDER or "console").strip().lower()
    platform_ctx = SendContext(
        provider=platform_provider,
        api_key=_platform_api_key(platform_provider),
        from_email=settings.EMAIL_FROM or "",
        from_name=settings.EMAIL_FROM_NAME or "",
        delivery_source="platform",
    )

    kind = _field_str(source_kind).lower()
    if kind and kind in SYSTEM_EMAIL_SOURCE_KINDS:
        return platform_ctx

    ws_id = _field_str(workspace_id) or _field_str(get_scope_key())
    if not ws_id or not await _is_org_workspace(ws_id):
        return platform_ctx

    record = await _get_record(ws_id)
    if (
        record is None
        or not record.is_enabled
        or not record.api_key_enc
        or record.provider not in _VALID_PROVIDERS
    ):
        return platform_ctx

    try:
        api_key = decrypt_secret_from_storage(record.api_key_enc)
    except Exception:
        logger.exception("workspace_email_delivery: decrypt failed ws=%s", ws_id)
        return platform_ctx

    if not api_key:
        return platform_ctx

    return SendContext(
        provider=record.provider,
        api_key=api_key,
        from_email=record.from_email or settings.EMAIL_FROM or "",
        from_name=record.from_name or settings.EMAIL_FROM_NAME or "",
        delivery_source="workspace",
    )


def _platform_api_key(provider: str) -> Optional[str]:
    if provider == "sendgrid":
        return settings.SENDGRID_API_KEY
    if provider == "resend":
        return settings.RESEND_API_KEY
    return None


async def touch_last_used(workspace_id: str) -> None:
    record = await _get_record(workspace_id)
    if record is None:
        return
    record.last_used_at = utc_now_iso()
    record.updated_at = record.last_used_at
    await record.save()
