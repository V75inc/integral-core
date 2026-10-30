"""Email enqueue requires the exact declared, approved operation and transaction."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import InsufficientPermissionsError
from app.schemas.access_delegation import ActionAuthorization
from app.schemas.policy import Decision, Subject
from app.schemas.workspace_email import EmailEnqueueResult
from app.services import app_operation_authority as authority
from app.services import email_delivery as delivery
from app.services.app_operations.context import OperationContext

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


@pytest.fixture
async def admitted(monkeypatch):
    spec = {
        "key": "notify",
        "kind": "execute",
        "authorization": {"roles": ["sender"], "effects": ["email.enqueue"]},
    }
    definition = {
        "id": "definition",
        "context": {
            "manifest_fingerprint": "hash",
            "canonical_manifest": {"app": {"operations": [spec]}},
        },
    }
    monkeypatch.setattr(
        authority, "current_definition", AsyncMock(return_value=({}, definition))
    )
    decision = AsyncMock(
        return_value=(Decision(allowed=True, reason="approved"), "author")
    )
    monkeypatch.setattr(authority, "operation_decision", decision)
    permit = authority.OperationPermit(
        principal_id="agent",
        workspace_id="ws",
        app_id="app",
        operation_key="notify",
        definition_id="definition",
        fingerprint="hash",
        payload={"recipient": "recipient@example.com"},
        declaration=ActionAuthorization.model_validate(spec["authorization"]),
        task=asyncio.current_task(),
        subject=Subject(kind="agent", id="agent"),
        delegator_id="author",
    )
    # Fixtures execute in their own task under pytest-asyncio. Install the
    # task-bound permit in each test, rather than leaking fixture ContextVars.
    return permit, definition, decision


async def call(ctx):
    return await ctx.enqueue_workspace_transactional_email(
        to="recipient@example.com",
        subject="Notice",
        text="Notice",
        html="<p>Notice</p>",
        source_kind="app_notice",
        idempotency_key="notice-1",
    )


def context(kind="agent"):
    return OperationContext(
        user_id="author",
        workspace_id="ws",
        scope="app:app",
        app_id="app",
        operation_key="notify",
        actor_kind=kind,
        actor_id=kind,
        read_only=False,
    )


@pytest.mark.parametrize("kind", ["agent", "connector", "human"])
async def test_declared_approved_effect_reaches_enqueue(admitted, monkeypatch, kind):
    permit, _, decision = admitted
    permit = replace(
        permit,
        principal_id=kind,
        subject=Subject(kind=kind, id=kind),
        task=asyncio.current_task(),
    )
    token = authority._permit.set(permit)
    queue = AsyncMock(return_value=EmailEnqueueResult(message_id="queued"))
    monkeypatch.setattr(delivery, "enqueue_email", queue)
    monkeypatch.setattr(
        "app.services.app_operations.transaction_scope.current_graph_transaction_database",
        lambda: object(),
    )
    try:
        assert (await call(context(kind)))["status"] == "queued"
        assert queue.call_args.args[0].actor_user_id == "author"
        assert (
            queue.call_args.kwargs["idempotency_key"] == "operation:app:notify:notice-1"
        )
        assert decision.call_args.kwargs["payload"] == permit.payload
    finally:
        authority._permit.reset(token)


@pytest.mark.parametrize(
    "change",
    [
        "approval",
        "effect",
        "definition",
        "workspace",
        "operation",
        "issuer",
        "readonly",
        "missing",
    ],
)
async def test_enqueue_refuses_unreviewed_or_revoked_authority(
    admitted, monkeypatch, change
):
    permit, definition, decision = admitted
    permit = replace(permit, task=asyncio.current_task())
    ctx = context()
    if change == "approval":
        decision.return_value = (
            Decision(allowed=False, reason="requires_human_approval"),
            "",
        )
    elif change == "effect":
        permit = replace(permit, declaration=ActionAuthorization(roles=["sender"]))
    elif change == "definition":
        definition["context"]["manifest_fingerprint"] = "changed"
    elif change == "workspace":
        ctx.workspace_id = "foreign"
    elif change == "operation":
        ctx.operation_key = "other"
    elif change == "issuer":
        decision.return_value = (Decision(allowed=True, reason="other issuer"), "other")
    elif change == "readonly":
        ctx.read_only = True
    token = authority._permit.set(None if change == "missing" else permit)
    queue = AsyncMock()
    monkeypatch.setattr(delivery, "enqueue_email", queue)
    monkeypatch.setattr(
        "app.services.app_operations.transaction_scope.current_graph_transaction_database",
        lambda: object(),
    )
    try:
        with pytest.raises(InsufficientPermissionsError):
            await call(ctx)
        queue.assert_not_awaited()
    finally:
        authority._permit.reset(token)


async def test_inherited_permit_cannot_enqueue_from_another_task(admitted, monkeypatch):
    permit, _, _ = admitted
    token = authority._permit.set(replace(permit, task=asyncio.current_task()))
    monkeypatch.setattr(
        "app.services.app_operations.transaction_scope.current_graph_transaction_database",
        lambda: object(),
    )
    queue = AsyncMock()
    monkeypatch.setattr(delivery, "enqueue_email", queue)
    try:
        with pytest.raises(InsufficientPermissionsError):
            await asyncio.create_task(call(context()))
        queue.assert_not_awaited()
    finally:
        authority._permit.reset(token)


async def test_enqueue_requires_transaction_before_effect(admitted, monkeypatch):
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
    )

    permit, _, _ = admitted
    token = authority._permit.set(replace(permit, task=asyncio.current_task()))
    queue = AsyncMock()
    monkeypatch.setattr(delivery, "enqueue_email", queue)
    try:
        with pytest.raises(OperationTransactionUnavailable):
            await call(context())
        queue.assert_not_awaited()
    finally:
        authority._permit.reset(token)


@pytest.mark.parametrize("same_profile", [True, False])
async def test_authenticated_alias_requires_the_same_profile(
    admitted, monkeypatch, same_profile
):
    from types import SimpleNamespace

    permit, _, _ = admitted
    token = authority._permit.set(replace(permit, task=asyncio.current_task()))
    queue = AsyncMock(return_value=EmailEnqueueResult(message_id="queued"))
    monkeypatch.setattr(delivery, "enqueue_email", queue)
    monkeypatch.setattr(
        "app.services.app_operations.transaction_scope.current_graph_transaction_database",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.services.permissions.get_user_node",
        AsyncMock(
            side_effect=[
                SimpleNamespace(id="profile"),
                SimpleNamespace(id="profile" if same_profile else "other"),
            ]
        ),
    )
    ctx = context()
    ctx.user_id = "auth-alias"
    try:
        if same_profile:
            assert (await call(ctx))["status"] == "queued"
        else:
            with pytest.raises(InsufficientPermissionsError):
                await call(ctx)
            queue.assert_not_awaited()
    finally:
        authority._permit.reset(token)
