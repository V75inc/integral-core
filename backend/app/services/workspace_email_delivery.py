"""Per-workspace transactional email delivery (Email Log app).

Configuration lives on the installed ``email_log`` App's ``settings`` (and is
also writable via ToolContext upsert). Core ``send_email`` routes attributed
workspace mail through these credentials when delivery is enabled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx

from app.models.nodes import App
from app.services.credential_crypto import (
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
    encryption_available,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

_SETTINGS_ENC_KEY = "api_key_enc"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _normalize_provider(raw: Any) -> str:
    p = str(raw or "resend").strip().lower()
    return p if p in ("resend", "sendgrid") else "resend"


def _enabled(settings: Dict[str, Any]) -> bool:
    return _truthy(settings.get("delivery_enabled")) or _truthy(
        settings.get("is_enabled")
    )


async def _email_log_app(workspace_id: str) -> Optional[App]:
    from app.exceptions import CrossAppTargetNotFoundError
    from app.services.relation_runtime import resolve_target_app

    try:
        app_id = await resolve_target_app(
            workspace_id=workspace_id, target_app_key="email_log"
        )
    except CrossAppTargetNotFoundError:
        return None
    return await App.get(app_id)


async def _load_settings(workspace_id: str) -> Dict[str, Any]:
    app_node = await _email_log_app(workspace_id)
    if not app_node:
        return {}
    return dict(getattr(app_node, "settings", None) or {})


def _api_key_from_settings(settings: Dict[str, Any], workspace_id: str) -> str:
    plain = str(settings.get("api_key") or "").strip()
    if plain:
        return plain
    enc = str(settings.get(_SETTINGS_ENC_KEY) or "").strip()
    if enc:
        return decrypt_secret_from_storage(enc, aad=workspace_id)
    return ""


@dataclass(frozen=True)
class ResolvedDelivery:
    provider: str
    from_email: str
    from_name: str
    api_key: str
    reply_to: Optional[str] = None


async def resolve_active_delivery(workspace_id: str) -> Optional[ResolvedDelivery]:
    """Return workspace delivery credentials when enabled and complete."""
    ws_id = str(workspace_id or "").strip()
    if not ws_id:
        return None
    settings = await _load_settings(ws_id)
    if not _enabled(settings):
        return None

    provider = _normalize_provider(
        settings.get("delivery_provider") or settings.get("provider")
    )
    from_email = str(settings.get("from_email") or "").strip()
    from_name = str(settings.get("from_name") or "").strip() or from_email
    reply_to = str(settings.get("reply_to") or "").strip() or None
    api_key = _api_key_from_settings(settings, ws_id)

    if not from_email or not api_key:
        logger.info(
            "workspace_email_delivery: incomplete config ws=%s enabled=1",
            ws_id,
        )
        return None
    return ResolvedDelivery(
        provider=provider,
        from_email=from_email,
        from_name=from_name,
        api_key=api_key,
        reply_to=reply_to,
    )


async def get_redacted(workspace_id: str) -> Dict[str, Any]:
    settings = await _load_settings(workspace_id)
    has_key = bool(_api_key_from_settings(settings, workspace_id))
    return {
        "workspace_id": workspace_id,
        "is_enabled": _enabled(settings),
        "provider": _normalize_provider(
            settings.get("delivery_provider") or settings.get("provider")
        ),
        "from_email": str(settings.get("from_email") or "").strip(),
        "from_name": str(settings.get("from_name") or "").strip(),
        "reply_to": str(settings.get("reply_to") or "").strip() or None,
        "api_key_configured": has_key,
    }


async def resolve_api_key_for_validation(
    workspace_id: str, payload: Dict[str, Any]
) -> Tuple[str, str]:
    body = dict(payload or {})
    provider = _normalize_provider(body.get("provider") or body.get("delivery_provider"))
    incoming = str(body.get("api_key") or "").strip()
    if incoming:
        return provider, incoming
    settings = await _load_settings(workspace_id)
    if "provider" in body or "delivery_provider" in body:
        pass
    else:
        provider = _normalize_provider(
            settings.get("delivery_provider") or settings.get("provider")
        )
    return provider, _api_key_from_settings(settings, workspace_id)


async def validate_delivery_provider(provider: str, api_key: str) -> Tuple[bool, str]:
    key = str(api_key or "").strip()
    if not key:
        return False, "API key is required"
    prov = _normalize_provider(provider)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if prov == "resend":
                resp = await client.get(
                    "https://api.resend.com/domains",
                    headers={"Authorization": f"Bearer {key}"},
                )
            else:
                resp = await client.get(
                    "https://api.sendgrid.com/v3/scopes",
                    headers={"Authorization": f"Bearer {key}"},
                )
        if resp.status_code == 401:
            return False, "Invalid API key"
        if resp.status_code >= 400:
            return False, f"Provider rejected key (HTTP {resp.status_code})"
        return True, "OK"
    except httpx.HTTPError as exc:
        logger.warning("validate_delivery_provider failed: %s", type(exc).__name__)
        return False, "Could not reach provider to validate key"


def _merge_settings(
    current: Dict[str, Any], body: Dict[str, Any], *, workspace_id: str
) -> Dict[str, Any]:
    out = dict(current or {})
    if "is_enabled" in body or "delivery_enabled" in body:
        out["delivery_enabled"] = _truthy(
            body.get("delivery_enabled", body.get("is_enabled"))
        )
    if "provider" in body or "delivery_provider" in body:
        out["delivery_provider"] = _normalize_provider(
            body.get("delivery_provider", body.get("provider"))
        )
    for field in ("from_email", "from_name", "reply_to"):
        if field in body and body[field] is not None:
            out[field] = str(body[field] or "").strip()

    incoming_key = body.get("api_key")
    if incoming_key is not None:
        key_str = str(incoming_key or "").strip()
        if key_str:
            if not encryption_available():
                raise ValueError(
                    "Credential encryption is not configured on this server"
                )
            out[_SETTINGS_ENC_KEY] = encrypt_secret_for_storage(
                key_str, aad=workspace_id
            )
        out.pop("api_key", None)
    elif _SETTINGS_ENC_KEY in out or "api_key" in out:
        out.pop("api_key", None)

    return out


async def upsert_delivery_config(
    *,
    workspace_id: str,
    actor_user_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    ws_id = str(workspace_id or "").strip()
    if not ws_id:
        return {"ok": False, "error": "workspace_id required"}

    app_node = await _email_log_app(ws_id)
    if not app_node:
        return {"ok": False, "error": "email_log app is not installed in this workspace"}

    body = dict(payload or {})
    try:
        merged = _merge_settings(
            dict(getattr(app_node, "settings", None) or {}),
            body,
            workspace_id=ws_id,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if _enabled(merged):
        if not str(merged.get("from_email") or "").strip():
            return {"ok": False, "error": "from_email is required when delivery is enabled"}
        provider = _normalize_provider(merged.get("delivery_provider"))
        api_key = _api_key_from_settings(merged, ws_id)
        valid, msg = await validate_delivery_provider(provider, api_key)
        if not valid:
            return {"ok": False, "error": msg}

    merged["delivery_updated_at"] = utc_now_iso()
    app_node.settings = merged
    app_node.updated_at = utc_now_iso()
    await app_node.save()

    _ = actor_user_id
    return {"ok": True, **(await get_redacted(ws_id))}
