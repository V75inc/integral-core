"""App settings redaction, secret merge on patch, and workspace email sync."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.exceptions import BadRequestError


def _schema_properties(schema: Dict[str, Any]) -> Dict[str, Any]:
    props = schema.get("properties") if isinstance(schema, dict) else None
    return dict(props) if isinstance(props, dict) else {}


def _is_secret_field(prop: Dict[str, Any]) -> bool:
    return str(prop.get("ui:widget") or "").strip() == "secret"


def redact_settings_for_response(
    settings: Dict[str, Any],
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    """Strip secret values before returning settings to clients."""
    out = dict(settings or {})
    for key, prop in _schema_properties(schema).items():
        if _is_secret_field(prop) and key in out:
            out[key] = ""
    return out


def merge_secret_fields_on_patch(
    existing: Dict[str, Any],
    incoming: Dict[str, Any],
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    """Blank secret fields in a PATCH keep the stored value."""
    merged = {**(existing or {}), **(incoming or {})}
    for key, prop in _schema_properties(schema).items():
        if not _is_secret_field(prop):
            continue
        if key not in incoming:
            continue
        if not str(incoming.get(key) or "").strip():
            if key in (existing or {}):
                merged[key] = existing[key]
            else:
                merged.pop(key, None)
    return merged


def is_workspace_email_delivery_settings(settings: Dict[str, Any]) -> bool:
    keys = set(settings or {})
    return "delivery_provider" in keys or (
        "delivery_enabled" in keys and ("from_email" in keys or "api_key" in keys)
    )


def build_workspace_delivery_sync_payload(
    settings: Dict[str, Any],
    settings_patch: Optional[Dict[str, Any]],
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    """Build upsert payload; omit secrets the user did not explicitly re-submit."""
    payload = dict(settings or {})
    patch = settings_patch or {}
    for key, prop in _schema_properties(schema or {}).items():
        if not _is_secret_field(prop):
            continue
        if key not in patch:
            payload.pop(key, None)
            continue
        if not str(patch.get(key) or "").strip():
            payload.pop(key, None)
    return payload


async def sync_workspace_email_delivery_from_app_settings(
    *,
    app_node: Any,
    actor_id: str,
    settings_patch: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Mirror Email Log-style app settings into WorkspaceEmailDelivery."""
    settings = dict(getattr(app_node, "settings", None) or {})
    if not is_workspace_email_delivery_settings(settings):
        return None
    schema = dict(getattr(app_node, "settings_schema", None) or {})
    payload = build_workspace_delivery_sync_payload(
        settings,
        settings_patch if settings_patch is not None else settings,
        schema,
    )
    from app.services.workspace_email_delivery import (
        upsert_delivery_config,
        workspace_has_delivery_record,
    )

    if not bool(settings.get("delivery_enabled", True)):
        ws_id = str(app_node.workspace_id or "")
        if not await workspace_has_delivery_record(ws_id):
            return None
        result = await upsert_delivery_config(
            workspace_id=ws_id,
            actor_user_id=actor_id,
            payload={**payload, "delivery_enabled": False},
        )
        if not result.get("ok"):
            raise BadRequestError(
                message=str(
                    result.get("error") or "Workspace email delivery update failed"
                ),
                details={"workspace_id": ws_id},
            )
        return result

    result = await upsert_delivery_config(
        workspace_id=str(app_node.workspace_id or ""),
        actor_user_id=actor_id,
        payload=payload,
    )
    if not result.get("ok"):
        raise BadRequestError(
            message=str(
                result.get("error") or "Workspace email delivery update failed"
            ),
            details={"workspace_id": app_node.workspace_id},
        )
    return result
