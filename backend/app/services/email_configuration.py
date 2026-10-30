"""Native email configuration. Mutations use workspace locks and revision CAS."""

from __future__ import annotations

import secrets
from typing import Any

from jvspatial.api.exceptions import InsufficientPermissionsError
from jvspatial.core.context import get_default_context

from app.exceptions import BadRequestError
from app.models.edges import HAS_EMAIL_CONFIGURATION
from app.models.nodes import Workspace, WorkspaceEmailConfiguration
from app.schemas.workspace_email import EmailConfiguration, SaveEmailDraft
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.credential_crypto import (
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
)
from app.services.workspace_permissions import is_workspace_admin_or_owner
from app.utils.time import utc_now_iso

SECRET_FIELDS = ("api_key", "smtp_password", "webhook_key")


async def require_admin(actor: str, workspace: str) -> None:
    """Require current workspace owner or administrator authority."""
    if (
        not actor
        or not workspace
        or not await is_workspace_admin_or_owner(actor, workspace)
    ):
        raise InsufficientPermissionsError("Workspace owner or admin required")


async def record(workspace_id: str, *, create: bool = False):
    """Read or provision the uniquely rooted configuration under a transaction."""
    db = get_default_context().database
    if create:
        locked = await db.find_one_and_update(
            "node",
            {"id": workspace_id, "entity": "Workspace"},
            {"$inc": {"context.storage_bytes_used": 0}},
        )
        if locked is None:
            raise BadRequestError("Workspace is unavailable")
    workspace = await Workspace.find_one({"id": workspace_id})
    if workspace is None:
        raise BadRequestError("Workspace is unavailable")
    identity = f"n.WorkspaceEmailConfiguration.{workspace_id}"
    found = await WorkspaceEmailConfiguration.find_one({"id": identity})
    if found is None and not create:
        return None
    if found is None:
        found = await WorkspaceEmailConfiguration.create(
            id=identity, workspace_id=workspace_id
        )
        await workspace.connect(found, edge=HAS_EMAIL_CONFIGURATION)
    edges = await get_default_context().find_edges_between(
        workspace_id, found.id, edge_class=HAS_EMAIL_CONFIGURATION
    )
    if found.workspace_id != workspace_id or len(edges) != 1:
        raise BadRequestError("Workspace email configuration needs repair")
    return found


def seal(configuration: EmailConfiguration, workspace: str, connection: str) -> dict:
    """Encrypt each credential with workspace and connection binding."""
    data = configuration.model_dump()
    for key in SECRET_FIELDS:
        data[key] = encrypt_secret_for_storage(
            data.get(key) or "", aad=f"email:{workspace}:{connection}:{key}"
        )
    return {"connection_id": connection, "configuration": data}


def unseal(snapshot: dict, workspace: str) -> EmailConfiguration:
    """Resolve bound credentials and refuse unreadable encrypted values."""
    data = dict(snapshot["configuration"])
    for key in SECRET_FIELDS:
        stored = data.get(key) or ""
        if stored and not stored.startswith("v1:"):
            raise BadRequestError("Email credentials need repair")
        data[key] = decrypt_secret_from_storage(
            stored, aad=f"email:{workspace}:{snapshot['connection_id']}:{key}"
        )
        if stored and not data[key]:
            raise BadRequestError("Email credentials are unavailable")
    return EmailConfiguration.model_validate(data)


def redacted(snapshot: dict | None) -> dict | None:
    """Project configuration without returning stored credentials."""
    if not snapshot:
        return None
    data = dict(snapshot["configuration"])
    for key in SECRET_FIELDS:
        data[f"{key}_configured"] = bool(data.pop(key, None))
    data["connection_id"] = snapshot["connection_id"]
    data["tracking_available"] = data["provider"] != "smtp"
    data["tracking_configured"] = (
        data["tracking_available"] and data["webhook_key_configured"]
    )
    from app.config import settings

    data["webhook_url"] = (
        f"{settings.EMAIL_WEBHOOK_BASE_URL.rstrip('/')}/api/workspace/email/webhooks/{snapshot['connection_id']}"
        if settings.EMAIL_WEBHOOK_BASE_URL
        else None
    )
    return data


def storage_available() -> bool:
    """Require transaction support for native credentials and durable queue writes."""
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )

    return graph_transaction_available(get_default_context().database)


def require_storage() -> None:
    """Refuse unsupported stores with a safe actionable error."""
    if not storage_available():
        raise BadRequestError(
            "Native workspace email requires PostgreSQL transactional storage"
        )


async def describe(actor: str, workspace: str) -> dict:
    """Return authorized redacted draft and active configuration state."""
    await require_admin(actor, workspace)
    found = await record(workspace)
    from app.config import settings

    provider = settings.EMAIL_PROVIDER.strip().lower()
    platform_ready = bool(settings.EMAIL_FROM) and (
        (provider == "resend" and bool(settings.RESEND_API_KEY))
        or (provider == "sendgrid" and bool(settings.SENDGRID_API_KEY))
        or (
            provider == "smtp"
            and bool(settings.SMTP_HOST)
            and (not settings.SMTP_USERNAME or bool(settings.SMTP_PASSWORD))
        )
    )
    return {
        "workspace_id": workspace,
        "platform_delivery_configured": platform_ready,
        "available": storage_available(),
        "revision": found.revision if found else 0,
        "tested_revision": found.tested_revision if found else None,
        "mode": "custom" if found and found.active else "platform",
        "draft": redacted(found.draft) if found else None,
        "active": redacted(found.active) if found else None,
    }


async def _cas(found: Any, fields: dict) -> None:
    updated = await get_default_context().database.find_one_and_update(
        "node",
        {"id": found.id, "context.revision": found.revision},
        {"$set": {f"context.{k}": v for k, v in fields.items()}},
    )
    if updated is None:
        raise BadRequestError("Email settings changed. Reload and try again.")


def check_revision(found: Any, revision: int) -> None:
    """Refuse stale configuration mutations."""
    if found.revision != revision:
        raise BadRequestError("Email settings changed. Reload and try again.")


async def _audit(actor: str, workspace: str, change: str, revision: int) -> None:
    from app.services.app_operations.event_outbox import insert_revision_event

    await insert_revision_event(
        transaction=get_default_context().database,
        workspace_id=workspace,
        namespace="workspace-email-v1",
        subject_id=workspace,
        revision=revision,
        event={
            "actor_kind": "human",
            "actor_id": actor,
            "action": "workspace.update",
            "resource_type": "Workspace",
            "resource_id": workspace,
            "before": None,
            "after": {
                "email_configuration": change,
                "email_configuration_revision": revision,
            },
            "scope": f"workspace:{workspace}",
        },
    )


async def save_draft(actor: str, workspace: str, body: SaveEmailDraft) -> dict:
    """Save a new immutable draft and invalidate earlier test evidence."""
    require_storage()
    async with postgres_graph_transaction():
        await require_admin(actor, workspace)
        found = await record(workspace, create=True)
        check_revision(found, body.expected_revision)
        config = body.configuration.model_copy(deep=True)
        # Null preserves the same provider's secret. Empty explicitly removes it.
        if found.draft and found.draft["configuration"]["provider"] == config.provider:
            for key in SECRET_FIELDS:
                if getattr(config, key) is None:
                    stored = found.draft["configuration"].get(key) or ""
                    if stored and not stored.startswith("v1:"):
                        raise BadRequestError(
                            "Replace or remove the unavailable stored credential"
                        )
                    value = decrypt_secret_from_storage(
                        stored,
                        aad=f"email:{workspace}:{found.draft['connection_id']}:{key}",
                    )
                    if stored and not value:
                        raise BadRequestError(
                            "Replace or remove the unavailable stored credential"
                        )
                    setattr(config, key, value)
        if found.draft and (
            not found.active
            or found.draft["connection_id"] != found.active["connection_id"]
        ):
            await get_default_context().database.find_one_and_update(
                "object",
                {"id": f"o.EmailConnection.{found.draft['connection_id']}"},
                {"$set": {"context.retired_at": utc_now_iso()}},
            )
        snapshot = seal(config, workspace, secrets.token_hex(16))
        snapshot["revision"] = found.revision + 1
        await _cas(
            found,
            {
                "draft": snapshot,
                "revision": found.revision + 1,
                "tested_revision": None,
                "updated_at": utc_now_iso(),
            },
        )
        await retain_connection(workspace, snapshot)
        await _audit(actor, workspace, "draft_saved", found.revision + 1)
    return await describe(actor, workspace)


async def retain_connection(workspace: str, snapshot: dict) -> None:
    # Retain verification authority for delayed events after provider replacement.
    """Retain webhook verification authority without sending credentials."""
    snapshot = {
        **snapshot,
        "configuration": {
            **snapshot["configuration"],
            "api_key": "",
            "smtp_password": "",
        },
    }
    await get_default_context().database.insert_if_absent(
        "object",
        {
            "id": f"o.EmailConnection.{snapshot['connection_id']}",
            "entity": "EmailConnection",
            "context": {
                "workspace_id": workspace,
                "snapshot": snapshot,
                "created_at": utc_now_iso(),
            },
        },
    )


async def test_draft(
    actor: str, workspace: str, revision: int, *, requested_recipient: str | None = None
) -> dict:
    """Send the exact saved draft to the selected or account recipient."""
    require_storage()
    await require_admin(actor, workspace)
    found = await record(workspace)
    if not found or not found.draft:
        raise BadRequestError("Save an email draft first")
    check_revision(found, revision)
    try:
        recipient = EmailConfiguration.address(requested_recipient or "")
    except ValueError:
        raise BadRequestError("Enter a single test recipient email address") from None
    if not recipient:
        from jvspatial.api.auth.models import User as AuthUser

        from app.services.permissions import get_user_node

        user = await get_user_node(actor)
        identity = await AuthUser.get(user.user_id) if user else None
        recipient = str(getattr(identity, "email", "") or "").strip()
    if not recipient:
        raise BadRequestError("Choose a test recipient email address")
    from app.services.email_delivery import test_configuration

    outcome = await test_configuration(workspace, actor, found.draft, recipient)
    if outcome.status == "accepted":
        async with postgres_graph_transaction():
            await require_admin(actor, workspace)
            current = await record(workspace, create=True)
            check_revision(current, revision)
            await _cas(current, {"tested_revision": revision})
    return {
        "status": outcome.status,
        "message": (
            f"Provider accepted the test email to {recipient}"
            if outcome.status == "accepted"
            else outcome.error
        ),
    }


async def activate(
    actor: str, workspace: str, revision: int, *, disable: bool = False
) -> dict:
    """Activate a tested revision or disable custom delivery atomically."""
    require_storage()
    async with postgres_graph_transaction():
        await require_admin(actor, workspace)
        found = await record(workspace, create=True)
        check_revision(found, revision)
        if not disable and (not found.draft or found.tested_revision != revision):
            raise BadRequestError(
                "Send a successful test for the saved draft before activation"
            )
        if found.active and (
            not found.draft
            or found.active["connection_id"] != found.draft["connection_id"]
        ):
            await get_default_context().database.find_one_and_update(
                "object",
                {"id": f"o.EmailConnection.{found.active['connection_id']}"},
                {"$set": {"context.retired_at": utc_now_iso()}},
            )
        if not disable:
            await get_default_context().database.find_one_and_update(
                "object",
                {"id": f"o.EmailConnection.{found.draft['connection_id']}"},
                {"$set": {"context.retired_at": None}},
            )
        await _cas(
            found,
            {
                "active": None if disable else found.draft,
                "delivery_generation": found.delivery_generation + 1,
                "revision": revision + 1,
                "tested_revision": None,
                "updated_at": utc_now_iso(),
            },
        )
        await _audit(
            actor, workspace, "disabled" if disable else "activated", revision + 1
        )
    return await describe(actor, workspace)


async def active_snapshot(workspace: str) -> dict | None:
    """Resolve the active native connection without Business App lookups."""
    found = await record(workspace)
    return found.active if found else None


async def delivery_binding(workspace: str) -> tuple[dict | None, int]:
    """Read provider selection and its routing generation from one stored image."""
    found = await record(workspace)
    return (found.active, found.delivery_generation) if found else (None, 0)
