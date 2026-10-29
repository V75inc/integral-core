"""Compatibility facade for native Core email settings (no Business dependency)."""

from dataclasses import dataclass
from typing import Optional

from app.schemas.workspace_email import EmailConfiguration, SaveEmailDraft
from app.services import email_configuration as native


@dataclass(frozen=True)
class ResolvedDelivery:
    provider: str
    from_email: str
    from_name: str
    api_key: str
    reply_to: Optional[str] = None
    configuration: Optional[EmailConfiguration] = None
    connection_id: str = "platform"


async def resolve_active_delivery(workspace_id: str) -> Optional[ResolvedDelivery]:
    snapshot = await native.active_snapshot(workspace_id)
    if not snapshot:
        return None
    config = native.unseal(snapshot, workspace_id)
    return ResolvedDelivery(
        config.provider,
        config.from_email,
        config.from_name,
        config.api_key or "",
        config.reply_to or None,
        config,
        snapshot["connection_id"],
    )


async def get_redacted(workspace_id: str) -> dict:
    found = await native.record(workspace_id)
    active = native.redacted(found.active) if found else None
    return {
        "workspace_id": workspace_id,
        "is_enabled": bool(active),
        **(active or {}),
        "settings_url": "/settings#email",
    }


async def upsert_delivery_config(
    *, workspace_id: str, actor_user_id: str, payload: dict
) -> dict:
    """Legacy upsert saves a draft; test and activation remain explicit."""
    await native.require_admin(actor_user_id, workspace_id)
    current = await native.describe(actor_user_id, workspace_id)
    enabled = payload.get("delivery_enabled", payload.get("is_enabled"))
    if enabled is not None and str(enabled).lower() in {"false", "0", "off", "no"}:
        result = await native.activate(
            actor_user_id,
            workspace_id,
            int(payload.get("expected_revision", current["revision"])),
            disable=True,
        )
        return {"ok": True, "status": "disabled", **result}
    mapping = {"delivery_provider": "provider"}
    clean = {
        mapping.get(k, k): v
        for k, v in payload.items()
        if k not in {"delivery_enabled", "is_enabled", "expected_revision"}
    }
    previous = current.get("draft") or {}
    values = {
        k: v
        for k, v in previous.items()
        if k in EmailConfiguration.model_fields and k not in native.SECRET_FIELDS
    }
    values.update(clean)
    try:
        revision = int(payload.get("expected_revision", current["revision"]))
        result = await native.save_draft(
            actor_user_id,
            workspace_id,
            SaveEmailDraft(
                expected_revision=revision,
                configuration=EmailConfiguration.model_validate(values),
            ),
        )
    except ValueError:
        return {"ok": False, "error": "Invalid email configuration"}
    return {
        "ok": True,
        "status": "draft",
        **result,
        "message": "Draft saved. Send a test and activate in Settings → Workspace → Email.",
        "settings_url": "/settings#email",
    }
