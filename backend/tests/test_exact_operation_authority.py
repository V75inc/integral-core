"""Adversarial exact App action permit boundaries."""

import asyncio

import pytest

from app.schemas.access_delegation import ActionAuthorization, ActionCondition
from app.services.access_delegation import conditions_match
from app.services.app_operation_authority import (
    operation_permit,
)
from app.services.app_operation_authority import permits_mutation as _permits_mutation
from app.services.resource_location import ResourceLocation

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


async def permits_mutation(**kwargs):
    kwargs.setdefault("principal_id", "p")
    return await _permits_mutation(**kwargs)


@pytest.fixture
def current_contract(monkeypatch):
    declaration = ActionAuthorization(
        roles=["approver"],
        targets=[{"input": "entry_id", "kind": "entry"}],
        mutations=[{"entry_type": "request", "fields": ["approval"], "kind": "update"}],
    )
    state = {
        "app": {"context": {"workspace_id": "ws"}},
        "definition": {
            "id": "def1",
            "context": {
                "manifest_fingerprint": "fp",
                "canonical_manifest": {
                    "app": {
                        "operations": [
                            {
                                "key": "approve",
                                "authorization": declaration.model_dump(),
                            }
                        ]
                    }
                },
            },
        },
    }

    async def current(_):
        return state["app"], state["definition"]

    async def decision(**kwargs):
        from app.schemas.policy import Decision

        return Decision(allowed=True, reason="test")

    async def locate(kind, identity):
        return ResourceLocation(
            workspace_id="ws",
            app_id="app" if identity != "foreign" else "other",
            track_id="track",
        )

    monkeypatch.setattr(
        "app.services.app_operation_authority.current_definition", current
    )
    monkeypatch.setattr(
        "app.services.app_operation_authority.action_decision", decision
    )
    monkeypatch.setattr("app.services.app_operation_authority.locate_resource", locate)
    return state


@pytest.mark.asyncio
async def test_exact_permit_cannot_be_used_for_other_fields_targets_or_delete(
    current_contract,
):
    async with operation_permit(
        principal_id="p",
        workspace_id="ws",
        app_id="app",
        operation_key="approve",
        payload={"entry_id": "entry"},
    ):
        common = dict(workspace_id="ws", entry_type_key="request", entry_id="entry")
        assert await permits_mutation(**common, fields={"approval"})
        assert not await permits_mutation(
            **common, fields={"approval"}, principal_id=""
        )
        assert not await permits_mutation(**common, fields={"other"})
        assert not await permits_mutation(
            **common, fields={"approval"}, top_level={"status"}
        )
        assert not await permits_mutation(
            **common, fields=set(), mutation_kind="delete"
        )
        assert not await permits_mutation(
            workspace_id="ws",
            entry_type_key="request",
            entry_id="other",
            fields={"approval"},
        )
        assert not await permits_mutation(
            workspace_id="ws",
            entry_type_key="request",
            entry_id="foreign",
            fields={"approval"},
        )
    assert not await permits_mutation(**common, fields={"approval"})


@pytest.mark.asyncio
async def test_permit_does_not_escape_to_child_task_and_definition_revision(
    current_contract,
):
    async with operation_permit(
        principal_id="p",
        workspace_id="ws",
        app_id="app",
        operation_key="approve",
        payload={"entry_id": "entry"},
    ):
        args = dict(
            workspace_id="ws",
            entry_type_key="request",
            entry_id="entry",
            fields={"approval"},
        )
        assert not await asyncio.create_task(permits_mutation(**args))
        current_contract["definition"]["id"] = "def2"
        assert not await permits_mutation(**args)


@pytest.mark.asyncio
async def test_ambient_flag_is_not_a_permit(monkeypatch):
    async def canonical(*args):
        return {"request": {"fields": ["approval"]}}

    monkeypatch.setattr(
        "app.services.app_invariant_guards._canonical_protected_state", canonical
    )
    from app.api.errors import BadRequestError
    from app.services.app_invariant_guards import (
        enforce_protected_field_write,
        register_protected_fields,
        reset_operation_write_active,
        set_operation_write_active,
        unregister_protected_fields,
    )

    register_protected_fields("ws", "app", {"request": ["approval"]})
    token = set_operation_write_active(True)
    try:
        with pytest.raises(BadRequestError):
            await enforce_protected_field_write(
                workspace_id="ws",
                entry_type_key="request",
                proposed_custom_fields={"approval": "yes"},
                entry_id="entry",
            )
    finally:
        reset_operation_write_active(token)
        unregister_protected_fields("ws", "app")


def test_conditions_use_server_state_and_each_complete_grant():
    targets = [
        {
            "context": {
                "author_id": "author",
                "status": "pending",
                "custom_fields": {"classification": "restricted"},
            }
        }
    ]
    assert conditions_match(
        [ActionCondition(attribute="entry.author_id", operator="ne", principal=True)],
        targets,
        "reviewer",
    )
    assert not conditions_match(
        [ActionCondition(attribute="entry.author_id", operator="ne", principal=True)],
        targets,
        "author",
    )
    assert not conditions_match(
        [ActionCondition(attribute="entry.status", value="approved")],
        targets,
        "reviewer",
    )
    assert not conditions_match(
        [ActionCondition(attribute="entry.status", value="pending")], [], "reviewer"
    )


def test_conditions_missing_state_cannot_satisfy_negative_conditions():
    assert not conditions_match(
        [ActionCondition(attribute="entry.author_id", operator="ne", principal=True)],
        [{"context": {}}],
        "reviewer",
    )


@pytest.mark.parametrize("value", [{"nested": "expression"}, ["x"] * 33])
def test_conditions_language_rejects_unbounded_values(value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ActionCondition(attribute="entry.status", value=value)


@pytest.mark.asyncio
async def test_top_level_and_delete_are_protected_without_custom_fields(monkeypatch):
    async def canonical(*args):
        return {
            "request": {
                "fields": ["approval"],
                "top_level": ["status"],
                "deny_delete": True,
            }
        }

    monkeypatch.setattr(
        "app.services.app_invariant_guards._canonical_protected_state", canonical
    )
    from app.api.errors import BadRequestError
    from app.services.app_invariant_guards import (
        enforce_protected_field_write,
        register_protected_fields,
        register_protected_state,
        unregister_protected_fields,
    )

    register_protected_fields("ws", "app", {"request": ["approval"]})
    register_protected_state(
        "ws",
        "app",
        {
            "app": {
                "protected_state": {
                    "request": {
                        "fields": ["approval"],
                        "top_level": ["status"],
                        "deny_delete": True,
                    }
                }
            }
        },
    )
    try:
        with pytest.raises(BadRequestError):
            await enforce_protected_field_write(
                workspace_id="ws",
                entry_type_key="request",
                proposed_custom_fields={},
                proposed_fields={"status": "approved"},
                entry_id="entry",
            )
        with pytest.raises(BadRequestError):
            await enforce_protected_field_write(
                workspace_id="ws",
                entry_type_key="request",
                proposed_custom_fields={},
                mutation_kind="delete",
                entry_id="entry",
            )
    finally:
        unregister_protected_fields("ws", "app")


@pytest.mark.asyncio
async def test_declared_target_resolver_is_read_only_and_cannot_change_other_payload(
    monkeypatch,
):
    from app.api.errors import BadRequestError, InsufficientPermissionsError
    from app.services.app_operations import dispatch

    state = {"allowed": True, "calls": 0, "resolved": {"entry_id": "exact-current"}}

    async def eligible(**kwargs):
        return state["allowed"]

    async def resolver(spec, payload, ctx):
        assert ctx.read_only is True
        state["calls"] += 1
        return state["resolved"]

    monkeypatch.setattr(
        "app.services.access_delegation.action_role_delegable", eligible
    )
    monkeypatch.setattr(
        dispatch,
        "get_workspace_tools",
        lambda _: {"resolve": {"_bundle_slug": "bundle"}},
    )
    monkeypatch.setattr(dispatch, "run_tool", resolver)
    spec = {
        "_bundle_slug": "bundle",
        "authorization": {
            "target_resolver": "resolve",
            "targets": [{"input": "entry_id", "kind": "entry"}],
        },
    }
    args = dict(
        user_id="p",
        workspace_id="ws",
        app_id="app",
        operation_key="act",
        spec=spec,
        payload={"tag": "human-readable", "entry_id": "forged"},
    )
    prepared = await dispatch.resolve_action_payload(**args)
    assert prepared == {"tag": "human-readable", "entry_id": "exact-current"}
    state["resolved"] = {"entry_id": "exact-current", "privileged_field": "invented"}
    with pytest.raises(BadRequestError):
        await dispatch.resolve_action_payload(**args)
    state["allowed"] = False
    before = state["calls"]
    with pytest.raises(InsufficientPermissionsError):
        await dispatch.resolve_action_payload(**args)
    assert state["calls"] == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "invoke",
        "notify",
        "notify_once",
        "create_entry_in_own_bundle_track",
        "create_entry_in_workspace_bundle_track",
        "call_app_tool",
        "call_connector_tool",
        "put_attachment",
        "clear_pending_onboarding_form_for_user",
        "upsert_workspace_email_delivery",
        "send_workspace_test_email",
        "send_workspace_transactional_email",
        "esign_vault_save",
        "esign_vault_delete",
        "esign_contract_decision",
    ],
)
async def test_service_context_cannot_borrow_human_generic_facade_writes(method):
    from app.api.errors import InsufficientPermissionsError
    from app.services.app_operations.context import OperationContext

    context = OperationContext(
        user_id="human-delegator",
        actor_kind="agent",
        actor_id="service",
        workspace_id="ws",
        app_id="app",
        scope="operation:app:approve",
    )
    with pytest.raises(InsufficientPermissionsError):
        await getattr(context, method)()


@pytest.mark.asyncio
async def test_namespace_approval_payload_cap_refuses_before_store_access():
    from app.exceptions import PayloadTooLargeError
    from app.schemas.policy import Subject
    from app.services.app_operation_approval import defer_app_operation

    with pytest.raises(PayloadTooLargeError):
        await defer_app_operation(
            subject=Subject(kind="agent", id="agent"),
            app_id="app",
            operation_key="approve",
            workspace_id="workspace",
            payload={"large": "x" * 1048577},
            original_input={},
            policy_id="policy",
            idempotency_key="request",
        )
