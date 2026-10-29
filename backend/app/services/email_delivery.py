"""Durable, tenant-bound email intents and fenced provider dispatch."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from jvspatial.api.exceptions import InsufficientPermissionsError
from jvspatial.core.context import get_default_context

from app.exceptions import BadRequestError
from app.schemas.workspace_email import EmailConfiguration, EmailEnqueueResult
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.credential_crypto import (
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
)
from app.services.email_configuration import delivery_binding, require_admin, unseal
from app.services.email_providers import DeliveryOutcome, deliver, validate_message
from app.utils.time import utc_now_iso


def message_object(identity: str) -> str:
    """Return the scalar identity for an email intent."""
    return f"o.EmailMessage.{identity}"


def database():
    """Use the database bound to this execution and transaction."""
    return get_default_context().database


def platform_configuration() -> EmailConfiguration:
    """Validate platform sending configuration from operator settings."""
    from app.config import settings

    return EmailConfiguration(
        provider=settings.EMAIL_PROVIDER,
        from_email=settings.EMAIL_FROM,
        from_name=settings.EMAIL_FROM_NAME,
        api_key=(
            settings.RESEND_API_KEY
            if settings.EMAIL_PROVIDER == "resend"
            else settings.SENDGRID_API_KEY
        ),
        smtp_host=settings.SMTP_HOST,
        smtp_port=settings.SMTP_PORT,
        smtp_username=settings.SMTP_USERNAME,
        smtp_password=settings.SMTP_PASSWORD,
        smtp_tls=settings.SMTP_TLS,
    )


async def _admit(actor: str, workspace: str) -> None:
    from app.services.workspace_permissions import can_access_workspace

    if not actor or await can_access_workspace(actor, workspace) not in {
        "owner",
        "admin",
        "member",
    }:
        raise InsufficientPermissionsError(
            "Current workspace membership is required to send email"
        )


async def _admit_source(message: Any) -> None:
    """Recheck source identity and live authority at enqueue and dispatch."""
    from app.models.nodes import Invitation, Notification

    if message.source_kind == "invitation":
        from app.services.invitation_authority import require_current_invitation_issuer

        invitation = await Invitation.find_one({"id": message.source_id})
        if (
            not invitation
            or invitation.workspace_id != message.workspace_id
            or invitation.status != "pending"
            or (invitation.adopted_by_user_id or invitation.invited_by_user_id)
            != message.actor_user_id
            or invitation.email.casefold() != message.to.casefold()
            or (
                invitation.expires_at
                and datetime.fromisoformat(invitation.expires_at)
                <= datetime.now(timezone.utc)
            )
        ):
            raise InsufficientPermissionsError("Invitation is no longer sendable")
        await require_current_invitation_issuer(invitation)
    if message.source_kind == "notification":
        notification = await Notification.find_one({"id": message.source_id})
        if (
            not notification
            or notification.user_id != message.actor_user_id
            or (notification.metadata or {}).get("email_workspace_id")
            != message.workspace_id
        ):
            raise InsufficientPermissionsError("Notification is no longer sendable")


async def enqueue_email(message: Any, *, idempotency_key: str) -> EmailEnqueueResult:
    """Enqueue trusted, already-authorized workspace mail; never return send success."""
    from app.agentive.services.work_items import enqueue_work_item, work_item_object_id
    from app.schemas.agentive.work import RetryPolicy
    from app.services.email_service import SENSITIVE_SOURCE_KINDS

    workspace, actor = str(message.workspace_id or ""), str(message.actor_user_id or "")
    if (
        not workspace
        or not actor
        or not idempotency_key
        or not message.source_kind
        or message.source_kind in SENSITIVE_SOURCE_KINDS
    ):
        raise BadRequestError(
            "Workspace email requires an actor, source and stable retry identity"
        )
    try:
        validate_message(message)
    except ValueError:
        raise BadRequestError("Invalid email address, header or message size") from None
    from app.services.email_configuration import require_storage

    require_storage()
    namespace = json.dumps([workspace, actor, idempotency_key], separators=(",", ":"))
    identity = hashlib.sha256(namespace.encode()).hexdigest()
    payload = json.dumps(asdict(message), sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    async with postgres_graph_transaction() as tx:
        await _admit(actor, workspace)
        await _admit_source(message)
        existing = await tx.get("object", message_object(identity))
        if existing:
            if existing["context"]["fingerprint"] != fingerprint:
                raise BadRequestError(
                    "Email retry identity was already used with different content"
                )
            return EmailEnqueueResult(message_id=identity)
        prior_work = await tx.get(
            "object",
            work_item_object_id(
                kind="email_dispatch",
                origin="workspace_email",
                principal_id=actor,
                workspace_id=workspace,
                idempotency_key=identity,
            ),
        )
        if prior_work:
            if (
                prior_work["context"]
                .get("input_payload", {})
                .get("message_fingerprint")
                != fingerprint
            ):
                raise BadRequestError(
                    "Email retry identity cannot be verified against the retained command"
                )
            # Replay the original enqueue receipt after history retention;
            # never create a new pending message for an already completed job.
            return EmailEnqueueResult(message_id=identity)
        snapshot, generation = await delivery_binding(workspace)
        if snapshot:
            config = unseal(snapshot, workspace)
            connection = snapshot["connection_id"]
        else:
            config = platform_configuration()
            connection = "platform"
        # Fail before retaining a queued intent if delivery is unconfigured.
        if (
            not config.from_email
            or (config.provider != "smtp" and not config.api_key)
            or (config.provider == "smtp" and not config.smtp_host)
        ):
            raise BadRequestError("Email delivery is not configured")
        row = {
            "id": message_object(identity),
            "entity": "EmailMessage",
            "context": {
                "message_id": identity,
                "workspace_id": workspace,
                "actor_user_id": actor,
                "source_kind": message.source_kind,
                "source_id": message.source_id,
                "to": message.to,
                "subject": message.subject,
                "provider": config.provider,
                "connection_id": connection,
                "delivery_generation": generation,
                "configuration_revision": (
                    snapshot.get("revision") if snapshot else None
                ),
                "configuration_fingerprint": hashlib.sha256(
                    config.model_dump_json().encode()
                ).hexdigest(),
                "fingerprint": fingerprint,
                "status": "queued",
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "payload_enc": encrypt_secret_for_storage(
                    payload, aad=f"email-payload:{workspace}:{identity}"
                ),
                "attempt": 0,
            },
        }
        result = await tx.insert_if_absent("object", row)
        if not result.created:
            other = await tx.get("object", row["id"])
            if other["context"]["fingerprint"] != fingerprint:
                raise BadRequestError(
                    "Email retry identity was already used with different content"
                )
        await enqueue_work_item(
            kind="email_dispatch",
            origin="workspace_email",
            principal_id=actor,
            workspace_id=workspace,
            idempotency_key=identity,
            input_payload={"message_id": identity, "message_fingerprint": fingerprint},
            deadline_at=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
            retry_policy=RetryPolicy(
                max_attempts=5, base_delay_seconds=60, max_delay_seconds=3600
            ),
            transaction=tx,
        )
    return EmailEnqueueResult(message_id=identity)


async def _finish(
    identity: str, outcome: DeliveryOutcome, *, fence: int, attempt: int
) -> None:
    now = utc_now_iso()
    terminal = not outcome.retryable or attempt >= 5
    changes = {
        "status": outcome.status if not outcome.retryable or terminal else "queued",
        "error": outcome.error,
        "provider_message_id": outcome.provider_message_id,
        "updated_at": now,
    }
    if terminal:
        changes["payload_enc"] = ""
    row = await database().get("object", message_object(identity))
    if row and row["context"].get("dispatch_fence") == fence:
        from app.services.email_events import STATES

        current = row["context"]["status"]
        if STATES.get(current, 0) > STATES.get(changes["status"], 0):
            changes["status"] = current
            changes["payload_enc"] = ""
        await database().find_one_and_update(
            "object",
            {
                "id": message_object(identity),
                "context.dispatch_fence": fence,
                "context.status": current,
            },
            {"$set": {f"context.{key}": value for key, value in changes.items()}},
        )
    await database().insert_if_absent(
        "object",
        {
            "id": f"o.EmailAttempt.{identity}.{attempt}",
            "entity": "EmailAttempt",
            "context": {
                "message_id": identity,
                "attempt": attempt,
                "workspace_id": row["context"].get("workspace_id") if row else None,
                "status": outcome.status,
                "error": outcome.error,
                "provider_message_id": outcome.provider_message_id,
                "created_at": now,
            },
        },
    )


async def run_email_work(item: Any, context: Any) -> None:
    """Dispatch an immutable intent under the current worker lease."""
    from app.agentive.services.work_execution import assert_effect_boundary_allowed
    from app.schemas.agentive.work import WorkError
    from app.services.email_service import (
        EmailMessage,
        _dispatch_workspace_email_hooks,
        _SendCredentials,
        _SendOutcome,
    )

    identity = str(item.input_payload.get("message_id") or "")
    row = await database().get("object", message_object(identity))
    if not row or (row["context"]["workspace_id"], row["context"]["actor_user_id"]) != (
        item.workspace_id,
        item.principal_id,
    ):
        raise WorkError(
            "email.scope_denied", "Email intent does not match worker scope"
        )
    data = row["context"]
    if data["status"] not in {"queued", "dispatching"}:
        return
    await assert_effect_boundary_allowed(context)
    if data["status"] == "dispatching":
        # Another lease may have crossed the network boundary. Never replay it.
        await database().find_one_and_update(
            "object",
            {"id": row["id"], "context.status": "dispatching"},
            {
                "$set": {
                    "context.status": "unknown",
                    "context.error": "Previous provider dispatch could not be confirmed",
                    "context.payload_enc": "",
                }
            },
        )
        raise WorkError(
            "email.outcome_unknown", "Previous dispatch could not be confirmed"
        )
    try:
        await _admit(item.principal_id, item.workspace_id)
        snapshot, generation = await delivery_binding(item.workspace_id)
        if (
            generation != data.get("delivery_generation", 0)
            or (snapshot["connection_id"] if snapshot else "platform")
            != data["connection_id"]
        ):
            raise BadRequestError("The sending provider was changed or disabled")
        config = (
            unseal(snapshot, item.workspace_id)
            if snapshot
            else platform_configuration()
        )
        if (
            data.get("configuration_fingerprint")
            != hashlib.sha256(config.model_dump_json().encode()).hexdigest()
        ):
            raise BadRequestError("The sending configuration changed")
        raw = decrypt_secret_from_storage(
            data["payload_enc"], aad=f"email-payload:{item.workspace_id}:{identity}"
        )
        if not raw:
            raise BadRequestError("Queued message content is unavailable")
        if (
            hashlib.sha256(raw.encode()).hexdigest() != data["fingerprint"]
            or item.input_payload.get("message_fingerprint") != data["fingerprint"]
        ):
            raise BadRequestError("Queued email no longer matches the accepted command")
        message = EmailMessage(**json.loads(raw))
        await _admit_source(message)
    except (BadRequestError, InsufficientPermissionsError, ValueError):
        await database().find_one_and_update(
            "object",
            {"id": row["id"], "context.status": "queued"},
            {
                "$set": {
                    "context.status": "failed",
                    "context.error": "Sending authority, provider or queued content is no longer available",
                    "context.payload_enc": "",
                }
            },
        )
        raise WorkError(
            "email.dispatch_denied", "Email sending authority is no longer available"
        )
    # A committed, durable intent precedes provider I/O; this is deliberately
    # outside the originating domain transaction. Recovery treats it as uncertain.
    async with postgres_graph_transaction():
        await assert_effect_boundary_allowed(context)
        claimed = await database().find_one_and_update(
            "object",
            {"id": row["id"], "context.status": "queued"},
            {
                "$set": {
                    "context.status": "dispatching",
                    "context.dispatch_fence": item.lease_fence,
                    "context.attempt": item.attempt,
                }
            },
        )
    if not claimed:
        return
    await assert_effect_boundary_allowed(context)
    try:
        await _admit(item.principal_id, item.workspace_id)
        await _admit_source(message)
        current, current_generation = await delivery_binding(item.workspace_id)
        if (
            current_generation != data.get("delivery_generation", 0)
            or (current["connection_id"] if current else "platform")
            != data["connection_id"]
        ):
            raise BadRequestError("The sending provider changed before dispatch")
    except (BadRequestError, InsufficientPermissionsError):
        async with postgres_graph_transaction():
            await assert_effect_boundary_allowed(context)
            await _finish(
                identity,
                DeliveryOutcome(
                    "failed", "Sending authority or provider changed before dispatch"
                ),
                fence=item.lease_fence,
                attempt=item.attempt,
            )
        raise WorkError(
            "email.dispatch_denied", "Sending authority changed before dispatch"
        ) from None
    outcome = await deliver(config, message, identity)
    async with postgres_graph_transaction():
        await assert_effect_boundary_allowed(context)
        await _finish(identity, outcome, fence=item.lease_fence, attempt=item.attempt)
    from app.services.email_events import reconcile_message

    await reconcile_message(identity)
    if outcome.retryable and item.attempt < 5:
        # The work kernel caps attempts and computes backoff. An explicit
        # provider wait is carried into next_attempt_at by the handler.
        failure = WorkError(
            "work.email_transient", "Temporary email provider rejection"
        )
        failure.retry_after = outcome.retry_after
        raise failure
    await _dispatch_workspace_email_hooks(
        message,
        config.provider,
        _SendCredentials(
            provider=config.provider,
            from_email=config.from_email,
            from_name=config.from_name,
        ),
        _SendOutcome(
            success=outcome.status == "accepted",
            error=outcome.error,
            provider_message_id=outcome.provider_message_id,
        ),
        identity,
        reply_to=message.reply_to or config.reply_to,
    )

    if outcome.status != "accepted":
        raise WorkError(
            (
                "email.outcome_unknown"
                if outcome.status == "unknown"
                else "email.send_failed"
            ),
            outcome.error,
        )


async def test_configuration(
    workspace: str, actor: str, snapshot: dict, recipient: str
) -> DeliveryOutcome:
    """Send a draft test directly and retain metadata without message bodies."""
    from app.services.email_service import EmailMessage

    identity = secrets.token_hex(16)
    config = unseal(snapshot, workspace)
    # Keep test outcomes in native history as well, without storing bodies.
    message = EmailMessage(
        to=recipient,
        subject="Integral — email delivery test",
        text="Your workspace email provider accepted this test request.",
        html="<p>This is a test of your workspace email provider.</p>",
    )
    await database().insert_if_absent(
        "object",
        {
            "id": message_object(identity),
            "entity": "EmailMessage",
            "context": {
                "message_id": identity,
                "workspace_id": workspace,
                "actor_user_id": actor,
                "source_kind": "email_test",
                "to": recipient,
                "subject": message.subject,
                "provider": config.provider,
                "connection_id": snapshot["connection_id"],
                "status": "dispatching",
                "dispatch_fence": 0,
                "created_at": utc_now_iso(),
                "payload_enc": "",
            },
        },
    )
    outcome = await deliver(config, message, identity)
    await _finish(identity, outcome, fence=0, attempt=1)
    from app.services.email_events import reconcile_message

    await reconcile_message(identity)
    return outcome


async def history(
    actor: str, workspace: str, *, cursor: str = "", limit: int = 25
) -> dict:
    """Page authorized delivery metadata without returning message content."""
    await require_admin(actor, workspace)
    limit = max(1, min(limit, 100))
    query = {"entity": "EmailMessage", "context.workspace_id": workspace}
    if cursor:
        previous = await database().get("object", cursor)
        if (
            not previous
            or previous.get("entity") != "EmailMessage"
            or previous["context"].get("workspace_id") != workspace
        ):
            raise BadRequestError("Invalid email history cursor")
        timestamp = previous["context"]["created_at"]
        query["$or"] = [
            {"context.created_at": {"$lt": timestamp}},
            {"context.created_at": timestamp, "id": {"$lt": cursor}},
        ]
    rows = await database().find(
        "object", query, sort=[("context.created_at", -1), ("id", -1)], limit=limit + 1
    )
    safe = (
        "message_id",
        "to",
        "subject",
        "source_kind",
        "provider",
        "status",
        "error",
        "created_at",
        "updated_at",
        "provider_message_id",
    )
    return {
        "items": [{k: r["context"].get(k) for k in safe} for r in rows[:limit]],
        "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
    }


async def retain_platform_attempt(message: Any, provider: str, outcome: Any) -> str:
    # Authentication mail is metadata-only, outside every workspace namespace.
    """Retain platform-only acceptance metadata without authentication content."""
    identity = secrets.token_hex(16)
    await database().insert_if_absent(
        "object",
        {
            "id": f"o.PlatformEmailAttempt.{identity}",
            "entity": "PlatformEmailAttempt",
            "context": {
                "provider": provider,
                "status": "accepted" if outcome.success else "failed",
                "created_at": utc_now_iso(),
            },
        },
    )
    return identity


async def retain_immediate_attempt(
    message: Any, provider: str, outcome: Any, *, connection_id: str
) -> str:
    """Retain metadata for legacy immediate calls without changing their booleans."""
    from app.services.email_service import SENSITIVE_SOURCE_KINDS

    if not message.workspace_id or message.source_kind in SENSITIVE_SOURCE_KINDS:
        return await retain_platform_attempt(message, provider, outcome)
    identity = secrets.token_hex(16)
    await database().insert_if_absent(
        "object",
        {
            "id": message_object(identity),
            "entity": "EmailMessage",
            "context": {
                "message_id": identity,
                "workspace_id": message.workspace_id,
                "actor_user_id": message.actor_user_id,
                "source_kind": message.source_kind,
                "source_id": message.source_id,
                "to": message.to,
                "subject": message.subject,
                "provider": provider,
                "connection_id": connection_id,
                "provider_message_id": outcome.provider_message_id or "",
                "status": getattr(outcome, "status", None)
                or ("accepted" if outcome.success else "failed"),
                "error": outcome.error,
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "payload_enc": "",
            },
        },
    )
    from app.services.email_events import reconcile_message

    await reconcile_message(identity)
    return identity


async def purge_email_history() -> None:
    """Expire delivery metadata and encrypted message payloads in bounded batches."""
    from app.config import settings

    now = datetime.now(timezone.utc)
    for entity in (
        "EmailMessage",
        "EmailAttempt",
        "EmailProviderEvent",
        "PlatformEmailAttempt",
        "EmailConnection",
    ):
        cutoff = (
            now - timedelta(days=max(1, settings.EMAIL_HISTORY_RETENTION_DAYS))
        ).isoformat()
        query = {"entity": entity, "context.created_at": {"$lt": cutoff}}
        if entity == "EmailConnection":
            # Current active/draft connections are never retired. Displaced
            # connections retain verification for 90 days after retirement.
            query["context.retired_at"] = {"$lt": cutoff}
        rows = await database().find("object", query, limit=500)
        for row in rows:
            await database().delete("object", row["id"])
    rows = await database().find(
        "object",
        {
            "entity": "EmailMessage",
            "context.payload_enc": {"$ne": ""},
            "$or": [
                {"context.created_at": {"$lt": (now - timedelta(days=7)).isoformat()}},
                {
                    "context.status": {"$in": ["queued", "dispatching"]},
                    "context.created_at": {
                        "$lt": (now - timedelta(hours=24)).isoformat()
                    },
                },
            ],
        },
        limit=500,
    )
    for row in rows:
        state = row["context"]["status"]
        changes = {"context.payload_enc": ""}
        if state in {"queued", "dispatching"}:
            changes.update(
                {
                    "context.status": "unknown" if state == "dispatching" else "failed",
                    "context.error": "Queued message expired",
                }
            )
        await database().find_one_and_update(
            "object", {"id": row["id"], "context.status": state}, {"$set": changes}
        )


async def delete_workspace_email(workspace_id: str) -> None:
    """Delete private email state with its workspace, inside the owning transaction."""
    while True:
        rows = await database().find(
            "object",
            {
                "entity": {
                    "$in": [
                        "EmailMessage",
                        "EmailAttempt",
                        "EmailConnection",
                        "EmailProviderEvent",
                    ]
                },
                "context.workspace_id": workspace_id,
            },
            limit=500,
        )
        if not rows:
            return
        for row in rows:
            await database().delete("object", row["id"])
