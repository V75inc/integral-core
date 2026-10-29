"""Explicit privileged migration of legacy Email Log delivery settings."""

from __future__ import annotations

import secrets

from jvspatial.core.context import get_default_context

from app.exceptions import BadRequestError
from app.models.nodes import App
from app.schemas.workspace_email import EmailConfiguration
from app.services import email_configuration as native
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.credential_crypto import decrypt_secret_from_storage
from app.utils.time import utc_now_iso


async def migrate_workspace(actor: str, workspace: str, *, apply: bool = False) -> dict:
    """Operators invoke explicitly; no boot-time App lookup or implicit mutation."""
    await native.require_admin(actor, workspace)
    apps = await App.find({"context.workspace_id": workspace})
    candidates = [
        app
        for app in apps
        if str(
            getattr(app, "installed_package_slug", "")
            or getattr(app, "source_operational_model_slug", "")
        ).replace("_", "-")
        == "email-log"
    ]
    if len(candidates) != 1:
        return {
            "status": "conflict" if candidates else "absent",
            "candidates": len(candidates),
        }
    app = candidates[0]
    settings = dict(app.settings or {})
    found = await native.record(workspace)
    if (
        found
        and settings.get("delivery_migrated_to_core")
        and not (settings.get("api_key") or settings.get("api_key_enc"))
    ):
        return {"status": "already_configured"}
    if found and found.active:
        return {
            "status": "conflict",
            "reason": "Native settings already exist; legacy credentials were not changed",
        }
    if not settings.get("api_key") and not settings.get("api_key_enc"):
        return {"status": "no_credentials"}
    key = str(settings.get("api_key") or "") or decrypt_secret_from_storage(
        str(settings.get("api_key_enc") or ""), aad=workspace
    )
    if not key:
        raise BadRequestError("Legacy email credential cannot be decrypted")
    config = EmailConfiguration(
        provider=settings.get("delivery_provider")
        or settings.get("provider")
        or "resend",
        from_email=settings.get("from_email") or "",
        from_name=settings.get("from_name") or "",
        reply_to=settings.get("reply_to") or "",
        api_key=key,
    )
    enabled = str(
        settings.get("delivery_enabled", settings.get("is_enabled", False))
    ).lower() in {"1", "true", "yes", "on"}
    if not apply:
        return {"status": "ready", "provider": config.provider, "enabled": enabled}
    parent_context = get_default_context()
    async with postgres_graph_transaction():
        await native.require_admin(actor, workspace)
        found = await native.record(workspace, create=True)
        if found.active or found.draft:
            raise BadRequestError(
                "Native email settings already exist; migration will not overwrite them"
            )
        snapshot = native.seal(config, workspace, secrets.token_hex(16))
        snapshot["revision"] = found.revision + 1
        snapshot["migrated_from"] = app.id
        # Preserve existing enabled behavior, without inventing test evidence.
        await native._cas(
            found,
            {
                "draft": snapshot,
                "active": snapshot if enabled else None,
                "delivery_generation": found.delivery_generation + int(enabled),
                "revision": found.revision + 1,
                "updated_at": utc_now_iso(),
            },
        )
        await native.retain_connection(workspace, snapshot)
        await native._audit(actor, workspace, "migrated", found.revision + 1)
        reread = await native.record(workspace)
        if native.unseal(reread.draft, workspace).api_key != key:
            raise BadRequestError("Native credential readback failed")
        # Match the complete old settings image; never save a stale whole App.
        clean = {
            k: v for k, v in settings.items() if k not in {"api_key", "api_key_enc"}
        }
        clean["delivery_migrated_to_core"] = True
        changed = await get_default_context().database.find_one_and_update(
            "node",
            {"id": app.id, "context.settings": {"$eq": settings}},
            {"$set": {"context.settings": clean}},
        )
        if not changed:
            raise BadRequestError("Legacy settings changed during migration")
    await parent_context.clear_cache()
    return {"status": "migrated", "provider": config.provider, "enabled": enabled}
