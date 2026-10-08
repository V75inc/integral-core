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


class _ReviewTransaction:
    """Transaction simulator; does not qualify PostgreSQL durability."""

    def __init__(self, records, fail_entity=""):
        self.records = deepcopy(records)
        self.fail_entity = fail_entity

    async def insert_if_absent(self, collection, document):
        from types import SimpleNamespace

        assert collection == "object"
        if document["entity"] == self.fail_entity:
            raise RuntimeError("injected persistence failure")
        existing = self.records.get(document["id"])
        if existing is not None:
            return SimpleNamespace(created=False, record=deepcopy(existing))
        self.records[document["id"]] = deepcopy(document)
        return SimpleNamespace(created=True, record=None)

    async def get(self, collection, object_id):
        assert collection == "object"
        return deepcopy(self.records.get(object_id))


class _ReviewDatabase:
    def __init__(self):
        self.records = {}
        self.fail_entity = ""
        self.commits = 0
        self.rollbacks = 0

    async def begin_transaction(self):
        return _ReviewTransaction(self.records, self.fail_entity)

    async def commit_transaction(self, transaction):
        self.records = transaction.records
        self.commits += 1

    async def rollback_transaction(self, transaction):
        self.rollbacks += 1


@pytest.fixture
def review_database(monkeypatch):
    from app.agentive.services import work_approvals, work_outbox

    db = _ReviewDatabase()
    monkeypatch.setattr(work_outbox, "_active_database", lambda: db)
    monkeypatch.setattr(work_outbox, "_is_postgres_txn_db", lambda _db: True)
    monkeypatch.setattr(work_outbox, "_txn_database", lambda _db: db)

    async def no_cache(_record):
        pass

    monkeypatch.setattr(work_outbox, "_refresh_work_item_cache", no_cache)
    monkeypatch.setattr(work_approvals, "_refresh_work_approval_cache", no_cache)
    return db


async def _propose_review(payload=None):
    from app.agentive.services.work_mandates import propose_work_mandate_review

    return await propose_work_mandate_review(
        revision=WorkMandateRevision.model_validate(payload or mandate_payload()),
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
    )


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_review_producer_commits_bound_pending_unit_and_reconciles_retry(
    review_database,
):
    item, approval = await _propose_review()
    assert item.status == "waiting_for_human"
    assert not item.lease_token and not item.next_attempt_at
    assert approval.status == "pending" and not approval.decision
    assert item.plan_revision == approval.authority_digest
    assert item.plan["mandate_approval_id"] == approval.work_approval_id
    assert item.deadline_at == approval.expires_at
    assert len(review_database.records) == 3
    outbox = next(
        d for d in review_database.records.values() if d["entity"] == "WorkOutboxEntry"
    )
    assert outbox["context"]["topic"] == "work.mandate_review_proposed"
    repeated, same_approval = await _propose_review()
    assert repeated == item and same_approval == approval
    assert len(review_database.records) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("entity", ["WorkApproval", "WorkOutboxEntry"])
async def test_review_producer_rolls_back_on_each_partial_insert(
    review_database, entity
):
    review_database.fail_entity = entity
    with pytest.raises(RuntimeError, match="injected persistence failure"):
        await _propose_review()
    assert not review_database.records
    assert review_database.rollbacks == 1
    assert review_database.commits == 0


@pytest.mark.asyncio
async def test_review_producer_changed_same_revision_conflicts(review_database):
    from app.schemas.agentive.work import WorkError

    await _propose_review()
    baseline = deepcopy(review_database.records)
    changed = mandate_payload()
    changed["goal"] = "Different reviewed intent"
    with pytest.raises(WorkError) as exc:
        await _propose_review(changed)
    assert exc.value.code == "work.idempotency_conflict"
    assert review_database.records == baseline
    changed["revision"] = 2
    next_item, _ = await _propose_review(changed)
    assert next_item.plan["mandate_revision"]["revision"] == 2
    assert len(review_database.records) == 6


@pytest.mark.asyncio
async def test_review_producer_never_recreates_missing_approval(review_database):
    from app.schemas.agentive.work import WorkError

    _, approval = await _propose_review()
    del review_database.records[approval.id]
    with pytest.raises(WorkError) as exc:
        await _propose_review()
    assert exc.value.code == "work.mandate_approval_required"
    assert approval.id not in review_database.records


@pytest.mark.asyncio
async def test_review_producer_retains_decided_retry(review_database):
    _, approval = await _propose_review()
    review_database.records[approval.id]["context"].update(
        status="rejected", decision="rejected", decider_id="user-1"
    )
    _, repeated = await _propose_review()
    assert repeated.status == "rejected" and repeated.decision == "rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["principal_id", "workspace_id", "thread_id"])
async def test_review_producer_scope_denied_before_storage(monkeypatch, field):
    from app.agentive.services import work_outbox
    from app.schemas.agentive.work import WorkError

    def no_database():
        pytest.fail("scope mismatch must be rejected before accessing storage")

    monkeypatch.setattr(work_outbox, "_active_database", no_database)
    payload = mandate_payload()
    payload[field] = "different-scope"
    with pytest.raises(WorkError) as exc:
        await _propose_review(payload)
    assert exc.value.code == "work.mandate_scope_denied"


@pytest.mark.asyncio
async def test_review_producer_requires_transactional_storage(monkeypatch):
    from app.agentive.services import work_outbox
    from app.schemas.agentive.work import WorkError

    monkeypatch.setattr(work_outbox, "_is_postgres_txn_db", lambda _db: False)
    with pytest.raises(WorkError) as exc:
        await _propose_review()
    assert exc.value.code == "work.transaction_required"


@pytest.mark.asyncio
async def test_ordinary_approval_cannot_queue_review(review_database, monkeypatch):
    from app.agentive.services import work_approvals
    from app.agentive.work_models import WorkItem
    from app.schemas.agentive.work import WorkError

    item, approval = await _propose_review()

    async def get_approval(_id):
        return approval

    async def get_item(_id):
        return item

    monkeypatch.setattr(work_approvals, "get_work_approval", get_approval)
    monkeypatch.setattr(WorkItem, "get", get_item)
    baseline = deepcopy(review_database.records)
    with pytest.raises(WorkError) as exc:
        await work_approvals.decide_work_approval_unit(
            work_approval_id=approval.work_approval_id,
            decision="approved",
            decider_id="user-1",
        )
    assert exc.value.code == "work.mandate_admission_required"
    assert review_database.records == baseline


@pytest.mark.asyncio
async def test_review_producer_equivalent_money_and_timezone_retry(review_database):
    item, approval = await _propose_review()
    equivalent = mandate_payload()
    equivalent["limits"]["max_spend"] = "1.00000000"
    equivalent["limits"]["deadline_at"] = "2030-01-01T06:00:00-04:00"
    same_item, same_approval = await _propose_review(equivalent)
    assert same_item == item and same_approval == approval
    assert len(review_database.records) == 3


@pytest.mark.asyncio
async def test_review_producer_expired_before_storage(monkeypatch):
    from app.agentive.services import work_outbox
    from app.schemas.agentive.work import WorkError

    def no_database():
        pytest.fail("expired intent must be rejected before storage")

    monkeypatch.setattr(work_outbox, "_active_database", no_database)
    payload = mandate_payload()
    payload["limits"]["deadline_at"] = "2000-01-01T10:00:00Z"
    with pytest.raises(WorkError) as exc:
        await _propose_review(payload)
    assert exc.value.code == "work.mandate_expired"


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_review_atomic_binding_and_duplicate_delivery():
    import asyncio
    import uuid

    from app.agentive.services import work_outbox
    from app.agentive.services.work_mandates import propose_work_mandate_review
    from app.agentive.work_models import WorkApproval, WorkItem, WorkOutboxEntry

    payload = mandate_payload()
    payload["mandate_id"] = f"pg-review-{uuid.uuid4().hex}"
    revision = WorkMandateRevision.model_validate(payload)

    async def propose():
        return await propose_work_mandate_review(
            revision=revision,
            principal_id="user-1",
            workspace_id="workspace-1",
            thread_id="thread-1",
        )

    first, second = await asyncio.gather(propose(), propose())
    assert first[0].id == second[0].id and first[1].id == second[1].id
    db = work_outbox._txn_database(work_outbox._active_database())
    # Bypass identity caches: committed store readback is the authority.
    item_doc = await db.get("object", first[0].id)
    approval_doc = await db.get("object", first[1].id)
    assert item_doc["context"]["status"] == "waiting_for_human"
    assert item_doc["context"]["plan_revision"] == revision.review_digest()
    assert approval_doc["context"]["authority_digest"] == revision.review_digest()
    assert approval_doc["context"]["status"] == "pending"
    assert (
        len(await WorkItem.find({"context.idempotency_key": first[0].idempotency_key}))
        == 1
    )
    assert (
        len(await WorkApproval.find({"context.work_item_id": first[0].work_item_id}))
        == 1
    )
    outboxes = await WorkOutboxEntry.find(
        {"context.work_item_id": first[0].work_item_id}
    )
    assert len(outboxes) == 1
    assert outboxes[0].topic == "work.mandate_review_proposed"


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("entity", ["WorkApproval", "WorkOutboxEntry"])
async def test_postgres_review_partial_failure_rolls_back(monkeypatch, entity):
    import uuid

    from app.agentive.services import work_outbox
    from app.agentive.services.work_items import work_item_object_id
    from app.agentive.services.work_mandates import propose_work_mandate_review

    payload = mandate_payload()
    payload["mandate_id"] = f"pg-rollback-{uuid.uuid4().hex}"
    revision = WorkMandateRevision.model_validate(payload)
    db = work_outbox._txn_database(work_outbox._active_database())
    original_begin = db.begin_transaction

    class FailureProxy:
        def __init__(self, transaction):
            self.transaction = transaction

        def __getattr__(self, name):
            return getattr(self.transaction, name)

        async def insert_if_absent(self, collection, document):
            if document["entity"] == entity:
                raise RuntimeError("injected persistence failure")
            return await self.transaction.insert_if_absent(collection, document)

    async def begin():
        return FailureProxy(await original_begin())

    monkeypatch.setattr(db, "begin_transaction", begin)
    with pytest.raises(RuntimeError, match="injected persistence failure"):
        await propose_work_mandate_review(
            revision=revision,
            principal_id="user-1",
            workspace_id="workspace-1",
            thread_id="thread-1",
        )
    object_id = work_item_object_id(
        kind="capability",
        origin="mandate_review",
        principal_id="user-1",
        workspace_id="workspace-1",
        idempotency_key=f"mandate:{revision.mandate_id}:1",
    )
    assert await db.get("object", object_id) is None
    from app.agentive.work_models import WorkApproval, WorkOutboxEntry

    bare_id = object_id.removeprefix("o.WorkItem.")
    assert await WorkApproval.find({"context.work_item_id": bare_id}) == []
    assert await WorkOutboxEntry.find({"context.work_item_id": bare_id}) == []


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_review_approval_is_held_but_rejection_is_durable():
    import uuid

    from app.agentive.services import work_approvals, work_outbox
    from app.schemas.agentive.work import WorkError

    payload = mandate_payload()
    payload["mandate_id"] = f"pg-admission-{uuid.uuid4().hex}"
    item, approval = await _propose_review(payload)
    with pytest.raises(WorkError) as exc:
        await work_approvals.decide_work_approval_unit(
            work_approval_id=approval.work_approval_id,
            decision="approved",
            decider_id="user-1",
        )
    assert exc.value.code == "work.mandate_admission_required"
    db = work_outbox._txn_database(work_outbox._active_database())
    assert (await db.get("object", item.id))["context"]["status"] == "waiting_for_human"
    assert (await db.get("object", approval.id))["context"]["status"] == "pending"
    rejected, stopped = await work_approvals.decide_work_approval_unit(
        work_approval_id=approval.work_approval_id,
        decision="rejected",
        decider_id="user-1",
    )
    assert rejected.status == "rejected" and stopped.status == "failed"
    assert (await db.get("object", approval.id))["context"]["decision"] == "rejected"
    repeated, same_approval = await _propose_review(payload)
    assert repeated.status == "failed" and same_approval.status == "rejected"
