"""Mandate review snapshots fail closed without claiming runtime authority."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas.agentive.work_mandate import WorkMandateRevision


def mandate_payload() -> dict:
    """Return a domain-neutral read-only review fixture."""
    return {
        "mandate_id": "mandate-1",
        "revision": 1,
        "principal_id": "user-1",
        "workspace_id": "workspace-1",
        "thread_id": "thread-1",
        "goal": "Summarize the reviewed records privately",
        "inputs": [{"kind": "entry", "resource_id": "entry-1"}],
        "grants": [
            {
                "capability_key": "records.read",
                "capability_version": "v1",
                "app_id": "app-1",
                "definition_id": "definition-1",
                "operation": "read",
                "resources": [{"kind": "entry", "resource_id": "entry-1"}],
                "max_calls": 2,
            }
        ],
        "model_routes": [
            {
                "provider": "provider-1",
                "model": "model-1",
                "credential_source": "workspace_byok",
                "credential_ref": "key-ref-1",
            }
        ],
        "limits": {
            "deadline_at": "2030-01-01T10:00:00Z",
            "max_model_requests": 3,
            "max_tool_calls": 4,
            "max_internal_writes": 0,
            "max_external_effects": 0,
            "max_spend": "1.00",
        },
        "success_criteria": ["A summary cites each source"],
        "stop_conditions": ["Ask if sources conflict"],
        "result_obligations": ["Return the summary and source receipts"],
    }


@pytest.mark.smoke
def test_review_is_deeply_immutable_and_round_trips() -> None:
    payload = mandate_payload()
    revision = WorkMandateRevision.model_validate(payload)
    payload["grants"][0]["resources"][0]["resource_id"] = "changed"
    assert revision.grants[0].resources[0].resource_id == "entry-1"
    assert isinstance(revision.grants, tuple)
    assert isinstance(revision.grants[0].resources, tuple)
    with pytest.raises(ValidationError):
        revision.limits.max_tool_calls = 99
    with pytest.raises(ValidationError):
        revision.grants[0].resources[0].resource_id = "changed"
    assert (
        WorkMandateRevision.model_validate_json(revision.model_dump_json()) == revision
    )


def test_digest_binds_every_reviewed_value_and_normalizes_money_time() -> None:
    payload = mandate_payload()
    first = WorkMandateRevision.model_validate(payload)
    equivalent = deepcopy(payload)
    equivalent["limits"]["max_spend"] = "1.0"
    equivalent["limits"]["deadline_at"] = "2030-01-01T06:00:00-04:00"
    assert (
        first.review_digest()
        == WorkMandateRevision.model_validate(equivalent).review_digest()
    )
    reversed_keys = json.dumps(dict(reversed(list(payload.items()))))
    assert (
        first.review_digest()
        == WorkMandateRevision.model_validate_json(reversed_keys).review_digest()
    )
    for field, value in (
        ("goal", "A changed goal"),
        ("principal_id", "user-2"),
        ("workspace_id", "workspace-2"),
        ("revision", 2),
    ):
        changed = deepcopy(payload)
        changed[field] = value
        assert (
            first.review_digest()
            != WorkMandateRevision.model_validate(changed).review_digest()
        )


@pytest.mark.parametrize("field", ["inputs", "grants", "model_routes"])
def test_duplicate_scope_rejected(field: str) -> None:
    payload = mandate_payload()
    payload[field].append(deepcopy(payload[field][0]))
    with pytest.raises(ValidationError):
        WorkMandateRevision.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "success_criteria",
        "stop_conditions",
        "result_obligations",
        "grants",
        "model_routes",
    ],
)
def test_required_review_sections_cannot_be_empty(field: str) -> None:
    payload = mandate_payload()
    payload[field] = []
    with pytest.raises(ValidationError):
        WorkMandateRevision.model_validate(payload)


@pytest.mark.parametrize(
    "spend", ["NaN", "Infinity", "-1", "0.000000001", "1000000000000000000"]
)
def test_invalid_spend_rejected(spend: str) -> None:
    payload = mandate_payload()
    payload["limits"]["max_spend"] = spend
    with pytest.raises(ValidationError):
        WorkMandateRevision.model_validate(payload)


def test_naive_deadline_and_unknown_approval_fields_rejected() -> None:
    payload = mandate_payload()
    payload["limits"]["deadline_at"] = "2030-01-01T10:00:00"
    with pytest.raises(ValidationError):
        WorkMandateRevision.model_validate(payload)
    payload = mandate_payload()
    payload["approved"] = True
    with pytest.raises(ValidationError):
        WorkMandateRevision.model_validate(payload)
    payload = mandate_payload()
    payload["model_routes"][0]["api_key"] = "not-a-secret-fixture"
    with pytest.raises(ValidationError):
        WorkMandateRevision.model_validate(payload)


def test_write_and_external_grants_require_corresponding_limits() -> None:
    payload = mandate_payload()
    payload["grants"][0]["operation"] = "internal_write"
    with pytest.raises(ValidationError):
        WorkMandateRevision.model_validate(payload)
    payload["limits"]["max_internal_writes"] = 1
    WorkMandateRevision.model_validate(payload)
    payload["grants"][0]["operation"] = "external_effect"
    with pytest.raises(ValidationError):
        WorkMandateRevision.model_validate(payload)
    payload["external_grants"] = [
        {
            "capability_key": "records.read",
            "provider": "provider-1",
            "credential_ref": "key-ref-1",
            "operation": "send",
            "destination": "reviewed-destination-1",
            "max_effects": 1,
        }
    ]
    with pytest.raises(ValidationError):
        WorkMandateRevision.model_validate(payload)
    payload["limits"]["max_external_effects"] = 1
    WorkMandateRevision.model_validate(payload)
    payload["external_grants"][0]["capability_key"] = "unreviewed.send"
    with pytest.raises(ValidationError):
        WorkMandateRevision.model_validate(payload)


def test_generic_capabilities_and_app_definition_binding() -> None:
    payload = mandate_payload()
    grant = payload["grants"][0]
    del grant["app_id"]
    with pytest.raises(ValidationError):
        WorkMandateRevision.model_validate(payload)
    del grant["definition_id"]
    grant["resources"] = [{"kind": "workspace", "resource_id": "workspace-1"}]
    revision = WorkMandateRevision.model_validate(payload)
    assert revision.grants[0].app_id is None
    assert revision.max_attempts == 1


def test_digest_does_not_depend_on_decimal_context() -> None:
    from decimal import localcontext

    payload = mandate_payload()
    payload["limits"]["max_spend"] = "12345.60"
    revision = WorkMandateRevision.model_validate(payload)
    expected = revision.review_digest()
    with localcontext() as context:
        context.prec = 3
        assert revision.review_digest() == expected


def approved_binding_fixture(monkeypatch):
    from app.agentive.services import work_mandates
    from app.agentive.work_models import WorkApproval, WorkItem

    revision = WorkMandateRevision.model_validate(mandate_payload())
    digest = revision.review_digest()
    item = WorkItem(
        id="o.WorkItem.work-1",
        work_item_id="work-1",
        status="queued",
        principal_id=revision.principal_id,
        workspace_id=revision.workspace_id,
        thread_id=revision.thread_id,
        plan_revision=digest,
        plan={
            "mandate_revision": revision.model_dump(mode="json"),
            "mandate_approval_id": "approval-1",
        },
    )
    approval = WorkApproval(
        id="o.WorkApproval.approval-1",
        work_approval_id="approval-1",
        work_item_id="work-1",
        authority_digest=digest,
        status="approved",
        decision="approved",
        decider_id="user-1",
        decided_at="2025-01-01T00:00:00Z",
        expires_at="2030-01-01T10:00:00Z",
    )

    async def get_item(object_id):
        assert object_id == item.id
        return item

    async def get_approval(approval_id):
        assert approval_id == "approval-1"
        return approval

    monkeypatch.setattr(WorkItem, "get", get_item)
    monkeypatch.setattr(work_mandates, "get_work_approval", get_approval)
    return work_mandates, item, approval, revision


@pytest.mark.asyncio
async def test_load_exact_approved_mandate_binding(monkeypatch) -> None:
    service, item, approval, revision = approved_binding_fixture(monkeypatch)
    loaded = await service.load_approved_work_mandate(
        work_item_id="work-1", principal_id="user-1", workspace_id="workspace-1"
    )
    assert loaded == revision
    assert item.status == "queued"
    assert approval.status == "approved"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope", [{"principal_id": "other-user"}, {"workspace_id": "other-workspace"}]
)
async def test_binding_scope_denial_precedes_approval_lookup(
    monkeypatch, scope
) -> None:
    from app.schemas.agentive.work import WorkError

    service, _, _, _ = approved_binding_fixture(monkeypatch)

    async def forbidden_lookup(_):
        pytest.fail("wrong scope must not read approval")

    monkeypatch.setattr(service, "get_work_approval", forbidden_lookup)
    arguments = {
        "work_item_id": "work-1",
        "principal_id": "user-1",
        "workspace_id": "workspace-1",
    }
    arguments.update(scope)
    with pytest.raises(WorkError, match="work.mandate_scope_denied"):
        await service.load_approved_work_mandate(**arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("work_item_id", "other-work"),
        ("authority_digest", "changed"),
        ("status", "pending"),
        ("status", "rejected"),
        ("decision", "rejected"),
        ("decider_id", ""),
        ("decided_at", ""),
        ("definition_id", "unreviewed-definition"),
    ],
)
async def test_binding_rejects_missing_or_mismatched_approval(
    monkeypatch, field, value
) -> None:
    from app.schemas.agentive.work import WorkError

    service, _, approval, _ = approved_binding_fixture(monkeypatch)
    setattr(approval, field, value)
    with pytest.raises(WorkError, match="work.mandate_approval_required"):
        await service.load_approved_work_mandate(
            work_item_id="work-1", principal_id="user-1", workspace_id="workspace-1"
        )


@pytest.mark.asyncio
async def test_binding_detects_changed_stored_review(monkeypatch) -> None:
    from app.schemas.agentive.work import WorkError

    service, item, _, _ = approved_binding_fixture(monkeypatch)
    item.plan["mandate_revision"]["goal"] = "Unreviewed expanded work"
    with pytest.raises(WorkError, match="work.mandate_revision_conflict"):
        await service.load_approved_work_mandate(
            work_item_id="work-1", principal_id="user-1", workspace_id="workspace-1"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expires,code",
    [
        ("2025-06-01T00:00:00Z", "work.mandate_expired"),
        ("not-a-date", "work.mandate_approval_invalid"),
        ("2030-01-01T00:00:00", "work.mandate_approval_invalid"),
    ],
)
async def test_binding_expiry_fails_closed(monkeypatch, expires, code) -> None:
    from app.schemas.agentive.work import WorkError

    service, _, approval, _ = approved_binding_fixture(monkeypatch)
    approval.expires_at = expires
    with pytest.raises(WorkError, match=code):
        await service.load_approved_work_mandate(
            work_item_id="work-1", principal_id="user-1", workspace_id="workspace-1"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["cancelled", "failed", "succeeded", "expired", "dead_letter"]
)
async def test_binding_terminal_work_cannot_resume(monkeypatch, status) -> None:
    from app.schemas.agentive.work import WorkError

    service, item, _, _ = approved_binding_fixture(monkeypatch)
    item.status = status
    with pytest.raises(WorkError, match="work.mandate_inactive"):
        await service.load_approved_work_mandate(
            work_item_id="work-1", principal_id="user-1", workspace_id="workspace-1"
        )
