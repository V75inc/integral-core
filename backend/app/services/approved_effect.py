"""Exact task-bound replay proof; human approval never replaces current authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncIterator

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.schemas.policy import Resource, Subject


def fact_fingerprint(value: dict) -> str:
    """Hash complete persisted intent with deterministic JSON and no coercion."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ApprovedEffect:
    approval_id: str
    fingerprint: str
    subject: Subject
    action: str
    resource: Resource
    payload: dict
    delegator_id: str
    events: list[dict]
    task: object
    database: Any
    app_operation: dict
    operation_outboxes: list[str]


_active: ContextVar[ApprovedEffect | None] = ContextVar(
    "integral_approved_effect", default=None
)


async def _current() -> ApprovedEffect | None:
    permit = _active.get()
    if permit is None:
        return None
    if (
        permit.task is not asyncio.current_task()
        or permit.database is not get_default_context().database
    ):
        raise AuthorizationUnavailableError()
    from app.models.nodes import Approval
    from app.services.authority_facts import primary_node_record

    row = await primary_node_record(permit.database, Approval, permit.approval_id)
    if (
        row is None
        or row["context"].get("status") != "approved"
        or fact_fingerprint(row) != permit.fingerprint
    ):
        raise AuthorizationUnavailableError()
    return permit


@asynccontextmanager
async def approved_effect_scope(
    *,
    approval_id: str,
    approval_fingerprint: str,
    original_subject: Subject,
    action: str,
    resource: Resource,
    payload: dict,
    delegator_id: str,
    events: list[dict] | None = None,
    app_operation: dict | None = None,
    operation_outboxes: list[str] | None = None,
) -> AsyncIterator[None]:
    """Enter only after atomic claim and current human/subject policy admission."""
    if _active.get() is not None:
        raise AuthorizationUnavailableError()
    token = _active.set(
        ApprovedEffect(
            approval_id,
            approval_fingerprint,
            original_subject,
            action,
            resource,
            dict(payload),
            delegator_id,
            events if events is not None else [],
            asyncio.current_task(),
            get_default_context().database,
            dict(app_operation or {}),
            operation_outboxes if operation_outboxes is not None else [],
        )
    )
    try:
        if await _current() is None:
            raise AuthorizationUnavailableError()
        yield
    finally:
        _active.reset(token)


async def approved_policy_replay(
    *,
    subject: Subject,
    action: str,
    resource: Resource,
    payload: dict | None,
    policy_id: str | None = None,
) -> bool:
    """Skip only the same approval intercept after current policy/ceiling match."""
    permit = await _current()
    return bool(
        permit is not None
        and policy_id is not None
        and (await permit.database.get("node", permit.approval_id))["context"][
            "policy_id"
        ]
        == policy_id
        and permit.subject == subject
        and permit.action == action
        and permit.resource == resource
        and payload is not None
        and fact_fingerprint(_normalized(action, permit.payload))
        == fact_fingerprint(_normalized(action, payload))
    )


def _normalized(action: str, payload: dict) -> dict:
    """Compare full mutation meaning; unknown mutation keys cannot be approved."""
    values = dict(payload)
    if "expected_revision" in values:
        revision = values.pop("expected_revision")
        if (
            "expected_record_revision" in values
            and values["expected_record_revision"] != revision
        ):
            raise AuthorizationUnavailableError()
        values["expected_record_revision"] = revision
    for metadata in ("expected_record_revision", "expected_schema_revision"):
        if metadata in values and values[metadata] is None:
            values.pop(metadata)
    if action == "entry.create":
        allowed = {
            "track_id",
            "title",
            "body",
            "description",
            "type_id",
            "custom_fields",
            "tags",
            "attachment_ids",
            "provenance",
        }
        if set(values) - allowed:
            raise AuthorizationUnavailableError()
        if "description" in values:
            if "body" in values and values["body"] != values["description"]:
                raise AuthorizationUnavailableError()
            values["body"] = values.pop("description")
        return {
            "track_id": values.get("track_id", ""),
            "title": values.get("title", ""),
            "body": values.get("body") or "",
            "type_id": values.get("type_id", ""),
            "custom_fields": values.get("custom_fields") or {},
            "tags": values.get("tags") or [],
            "attachment_ids": values.get("attachment_ids") or [],
            **(
                {"provenance": values["provenance"]} if values.get("provenance") else {}
            ),
        }
    if action == "entry.update":
        allowed = {
            "entry_id",
            "title",
            "body",
            "description",
            "type_id",
            "custom_fields",
            "tags",
            "attachment_ids",
            "expected_revision",
            "expected_record_revision",
            "expected_schema_revision",
            "status",
        }
        if set(values) - allowed:
            raise AuthorizationUnavailableError()
        if "description" in values:
            if "body" in values and values["body"] != values["description"]:
                raise AuthorizationUnavailableError()
            values["body"] = values.pop("description")
        return values
    if action == "entry.delete" and set(values) - {
        "entry_id",
        "expected_revision",
        "expected_record_revision",
        "expected_schema_revision",
    }:
        raise AuthorizationUnavailableError()
    return values


async def approved_effect_principal(
    *,
    actor_kind: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    payload: dict | None,
) -> str | None:
    """Return the locked delegator only for this exact original current mutation."""
    permit = await _current()
    if permit is None:
        return None
    if permit.app_operation:
        # App effects use the exact operation permit at the canonical Entry gate.
        return None
    if (
        permit.subject.kind != actor_kind
        or permit.subject.id != actor_id
        or permit.action != action
        or payload is None
    ):
        raise AuthorizationUnavailableError()
    if action == "entry.create":
        expected_kind, expected_id = "track", permit.payload.get("track_id")
    elif action in {"entry.update", "entry.delete", "comment.create"}:
        expected_kind, expected_id = "entry", permit.payload.get("entry_id")
    elif action == "app.create":
        expected_kind, expected_id = "workspace", permit.payload.get("workspace_id")
    elif action == "track.create":
        expected_kind, expected_id = (
            ("app", permit.payload["app_id"])
            if permit.payload.get("app_id")
            else ("workspace", permit.payload.get("workspace_id"))
        )
    else:
        raise AuthorizationUnavailableError()
    if (resource_type, resource_id) != (expected_kind, expected_id) or fact_fingerprint(
        _normalized(action, payload)
    ) != fact_fingerprint(_normalized(action, permit.payload)):
        raise AuthorizationUnavailableError()
    return permit.delegator_id


async def capture_approved_change_event(event: dict) -> bool:
    """Capture current task's actual bound effects without publishing before commit."""
    permit = await _current()
    if permit is None or event.get("action") == "policy.deny":
        return False
    from app.services.resource_location import KNOWN_APP_STATES, locate_resource

    expected_scope = permit.resource.scope.split(":", 1)
    if len(expected_scope) != 2:
        raise AuthorizationUnavailableError()
    expected = await locate_resource(
        expected_scope[0], expected_scope[1], allowed_app_states=KNOWN_APP_STATES
    )
    scope = event.get("scope")
    parts = scope.split(":", 1) if isinstance(scope, str) else []
    if (
        len(parts) != 2
        or parts[0] not in {"workspace", "app", "track", "entry"}
        or not parts[1]
    ):
        raise AuthorizationUnavailableError()
    actual = await locate_resource(
        parts[0], parts[1], allowed_app_states=KNOWN_APP_STATES
    )
    if (
        expected is None
        or actual is None
        or actual.workspace_id != expected.workspace_id
    ):
        raise AuthorizationUnavailableError()
    persisted = {key: value for key, value in event.items() if key != "_internal_actor"}
    permit.events.append(
        {**persisted, "actor_kind": permit.subject.kind, "actor_id": permit.subject.id}
    )
    return True


def approved_event_sink(event: dict) -> None:
    """Collect a canonical helper's event on its already admitted synchronous task."""
    permit = _active.get()
    if (
        permit is None
        or permit.task is not asyncio.current_task()
        or permit.database is not get_default_context().database
    ):
        raise AuthorizationUnavailableError()
    permit.events.append(
        {**event, "actor_kind": permit.subject.kind, "actor_id": permit.subject.id}
    )


async def approved_capture_active() -> bool:
    """Expose current task/database/primary-fingerprint proof to canonical producers."""
    return await _current() is not None


async def validate_app_replay(
    *,
    subject: Subject,
    app_id: str,
    operation_key: str,
    workspace_id: str,
    original_input: dict,
    resolved_input: dict,
) -> None:
    """A deferred invocation cannot retarget its selector or definition on replay."""
    permit = await _current()
    if permit is None or not permit.app_operation:
        return
    binding = permit.app_operation
    if (
        permit.subject != subject
        or binding["app_id"] != app_id
        or binding["operation_key"] != operation_key
        or binding["workspace_id"] != workspace_id
        or fact_fingerprint(binding["original_input"])
        != fact_fingerprint(original_input)
        or fact_fingerprint(permit.payload) != fact_fingerprint(resolved_input)
    ):
        raise AuthorizationUnavailableError()


async def defer_approved_operation_delivery(outbox_id: str) -> bool:
    """Release a bound App receipt event only after the outer decision commits."""
    permit = await _current()
    if permit is None:
        return False
    if not permit.app_operation or not outbox_id:
        raise AuthorizationUnavailableError()
    if outbox_id not in permit.operation_outboxes:
        permit.operation_outboxes.append(outbox_id)
    return True
