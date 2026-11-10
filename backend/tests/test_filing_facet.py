"""W2.2: an existing person is updated or linked, not duplicated."""

from __future__ import annotations

import asyncio

import pytest

from app.models.edges import CONTAINS, IS_MEMBER_OF, OWNS, REFERENCES
from app.models.nodes import App, Entry, Tag, Track, User, Workspace
from app.services.agent_scope import current_scope_workspace_id
from app.services.app_invariant_guards import (
    register_protected_fields,
    unregister_protected_fields,
)
from app.services.app_operations.transaction_scope import graph_transaction_available
from app.services.destination_rank import rank_destinations
from app.services.filing_facet import (
    apply_filing_facet,
    reset_filing_idempotency,
    set_facet_probe,
)
from app.utils.time import utc_now_iso


async def _world():
    from app.services.app_graph import (
        catalog_app,
        catalog_track,
        catalog_user,
        catalog_workspace,
    )

    now = utc_now_iso()
    user = await User.create(
        user_id="facet-user",
        display_name="Facet User",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(user)
    workspace = await Workspace.create(
        kind="personal",
        workspace_type="personal",
        name="Facet Workspace",
        name_fold="facet workspace",
        created_at=now,
        updated_at=now,
    )
    await user.connect(workspace, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    await catalog_workspace(workspace)
    app = await App.create(
        name="Open Books",
        owner_user_id=user.id,
        workspace_id=workspace.id,
        visibility="private",
        created_at=now,
        updated_at=now,
    )
    await user.connect(app, edge=OWNS, role="owner", granted_at=now)
    await catalog_app(app)

    async def _track(title):
        track = await Track.create(
            title=title,
            owner_id=user.id,
            workspace_id=workspace.id,
            visibility="private",
            created_at=now,
            updated_at=now,
        )
        await user.connect(track, edge=OWNS, role="owner", granted_at=now)
        await app.connect(track, edge=CONTAINS, added_at=now)
        await catalog_track(track)
        return track

    async def _entry(track, title, **fields):
        entry = await Entry.create(
            title=title,
            author_id=user.id,
            track_id=track.id,
            created_at=now,
            updated_at=now,
            record_revision=1,
            schema_revision=1,
            **fields,
        )
        await track.connect(entry, edge=CONTAINS, added_at=now)
        return entry

    contacts = await _track("Contacts")
    invoices = await _track("Invoices")
    ada = await _entry(
        contacts,
        "Ada Lovelace",
        custom_fields={"email": "ada@studio.test", "phone": "555-0100"},
    )
    invoice = await _entry(invoices, "Studio invoice", custom_fields={"amount": "1200"})
    vip = await Tag.create(
        name="vip",
        name_fold="vip",
        track_id=invoices.id,
        created_at=now,
    )
    await invoices.connect(vip, edge=CONTAINS, added_at=now)
    return {
        "user": user,
        "workspace": workspace,
        "app": app,
        "contacts": contacts,
        "ada": ada,
        "invoice": invoice,
        "vip": vip,
    }


def _base(entry, **extra):
    payload = {
        "entry_id": entry.id,
        "expected_record_revision": entry.record_revision,
        "expected_schema_revision": entry.schema_revision,
        "entry_type_key": "contact",
    }
    payload.update(extra)
    return payload


@pytest.mark.asyncio
async def test_known_person_is_updated_not_duplicated():
    """A matching person is named, then patched without a second record."""
    world = await _world()
    token = current_scope_workspace_id.set(world["workspace"].id)
    try:
        ranked = await rank_destinations(
            world["user"].id,
            text="Ada Lovelace called about the studio invoice",
        )
    finally:
        current_scope_workspace_id.reset(token)
    likely = ranked["facets"][0]["likely_entries"]
    assert likely[0]["entry_id"] == world["ada"].id
    assert any("lovelace" in reason for reason in likely[0]["reasons"])

    result = await apply_filing_facet(
        world["user"].id,
        _base(
            world["ada"],
            mode="update",
            fields={"phone": "555-0199"},
            text="Ada Lovelace called",
        ),
    )
    assert result["filed"] is True
    assert result["partial"] is False
    fresh = await Entry.get(world["ada"].id)
    assert fresh.custom_fields["phone"] == "555-0199"
    assert fresh.custom_fields["email"] == "ada@studio.test"
    rows = await world["contacts"].nodes(edge=[CONTAINS], node=["Entry"], limit=10)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_stale_revision_conflicts_and_retry_appends_once():
    """A concurrent edit conflicts. The same append payload lands once."""
    reset_filing_idempotency()
    world = await _world()
    stale = await apply_filing_facet(
        world["user"].id,
        _base(
            world["ada"],
            mode="update",
            fields={"phone": "555-0000"},
            expected_record_revision=9,
            text="stale",
        ),
    )
    assert stale["filed"] is False
    assert stale["error_code"] == "record_revision_conflict"
    assert stale["partial"] is False
    unchanged = await Entry.get(world["ada"].id)
    assert unchanged.custom_fields["phone"] == "555-0100"

    payload = _base(
        world["ada"],
        mode="append",
        append_field="notes",
        text="follow up Friday",
        idempotency_key="facet-retry-1",
    )
    first = await apply_filing_facet(world["user"].id, payload)
    second = await apply_filing_facet(world["user"].id, payload)
    assert first["filed"] is True
    assert second["retry"] is True
    fresh = await Entry.get(world["ada"].id)
    assert fresh.custom_fields["notes"] == "follow up Friday"
    assert fresh.custom_fields["notes"].count("follow up Friday") == 1


@pytest.mark.asyncio
async def test_protected_field_is_refused():
    """A protected field needs the App operation. The generic patch does not land."""
    world = await _world()
    register_protected_fields(
        world["workspace"].id, world["app"].id, {"contact": ["salary"]}
    )
    try:
        result = await apply_filing_facet(
            world["user"].id,
            _base(world["ada"], mode="update", fields={"salary": "10"}, text="pay"),
        )
    finally:
        unregister_protected_fields(world["workspace"].id, world["app"].id)
    assert result["filed"] is False
    assert result["error_code"] == "protected_field_write"
    assert result["partial"] is False
    fresh = await Entry.get(world["ada"].id)
    assert "salary" not in (fresh.custom_fields or {})


@pytest.mark.asyncio
async def test_multi_write_facet_refused_without_transaction():
    """Relations plus tags refuse before any write when the store has no txn."""
    if graph_transaction_available():
        pytest.skip("transaction backend applies the facet instead of refusing")
    world = await _world()
    result = await apply_filing_facet(
        world["user"].id,
        _base(
            world["invoice"],
            mode="update",
            text="link the person",
            relations=[{"field_key": "contact", "target_entry_id": world["ada"].id}],
            tags=["vip"],
            entry_type_key="invoice",
        ),
    )
    assert result["filed"] is False
    assert result["partial"] is False
    assert result["error_code"] == "transaction_unavailable"
    fresh = await Entry.get(world["invoice"].id)
    assert fresh.custom_fields == {"amount": "1200"}
    assert fresh.record_revision == 1
    linked = await fresh.nodes(edge=[REFERENCES], direction="out", node=["Entry"])
    assert linked == []


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_tag_failure_rolls_back_the_link():
    """A failure while applying tags undoes the relation written in the same facet."""
    world = await _world()

    def _boom(step):
        if step == "tags":
            raise RuntimeError("tag write failed")

    set_facet_probe(_boom)
    try:
        result = await apply_filing_facet(
            world["user"].id,
            _base(
                world["invoice"],
                mode="update",
                text="link the person",
                relations=[
                    {"field_key": "contact", "target_entry_id": world["ada"].id}
                ],
                tags=["vip"],
                entry_type_key="invoice",
            ),
        )
    finally:
        set_facet_probe(None)
    assert result["filed"] is False
    assert result["partial"] is False
    assert result["rolled_back"] is True
    fresh = await Entry.get(world["invoice"].id)
    assert fresh.custom_fields == {"amount": "1200"}
    assert fresh.record_revision == 1
    linked = await fresh.nodes(edge=[REFERENCES], direction="out", node=["Entry"])
    assert linked == []


@pytest.mark.asyncio
@pytest.mark.parametrize("same_request", [False, True])
async def test_parallel_approved_appends_serialize_at_the_entry_revision(
    monkeypatch, same_request
):
    if not graph_transaction_available():
        pytest.skip("requires PostgreSQL row locks")

    from app.services import filing_facet

    world = await _world()
    entry = world["ada"]
    preflight = filing_facet._resolve_relations
    both_ready = asyncio.Barrier(2)

    async def meet_after_revision_check(*args):
        result = await preflight(*args)
        await both_ready.wait()
        return result

    monkeypatch.setattr(filing_facet, "_resolve_relations", meet_after_revision_check)

    async def append(text):
        return await apply_filing_facet(
            world["user"].id,
            _base(
                entry,
                mode="append",
                append_field="body",
                text=text,
                idempotency_key=text,
            ),
        )

    second_text = "closure one" if same_request else "closure two"
    first, second = await asyncio.wait_for(
        asyncio.gather(append("closure one"), append(second_text)), timeout=15
    )
    if same_request:
        assert first["filed"] and second["filed"]
        assert sum(bool(result.get("retry")) for result in (first, second)) == 1
    else:
        assert sum(result["filed"] for result in (first, second)) == 1
        assert {first.get("error_code"), second.get("error_code")} == {
            None,
            "record_revision_conflict",
        }
    fresh = await Entry.find_one({"id": entry.id})
    assert fresh.record_revision == entry.record_revision + 1
    assert (
        sum((fresh.body or "").count(text) for text in ("closure one", "closure two"))
        == 1
    )


@pytest.mark.asyncio
async def test_reviewed_append_checks_supporting_entry_at_application():
    from app.agentive.tooling.stagers_filing import _stage_existing_entry

    world = await _world()
    goal, source = world["ada"], world["invoice"]
    payload = _base(
        goal,
        mode="append",
        append_field="body",
        text="Goal achieved",
        evidence_revisions={source.id: source.record_revision},
        idempotency_key="goal-closure",
    )
    staged = await _stage_existing_entry(payload, mode="append", text=payload["text"])
    assert staged["payload"]["evidence_revisions"] == payload["evidence_revisions"]
    source.record_revision += 1
    await source.save()
    denied = await apply_filing_facet(world["user"].id, staged["payload"])
    fresh = await Entry.find_one({"id": goal.id})
    assert fresh.record_revision == goal.record_revision
    if not graph_transaction_available():
        assert denied["error_code"] == "transaction_unavailable"
        return
    assert denied["error_code"] == "record_revision_conflict"

    refreshed = {**payload, "evidence_revisions": {source.id: source.record_revision}}
    result = await apply_filing_facet(world["user"].id, refreshed)
    assert result["filed"] is True, result
    source.record_revision += 1
    await source.save()
    replay = await apply_filing_facet(world["user"].id, refreshed)
    assert replay["retry"] is True
    assert (await Entry.find_one({"id": goal.id})).body.count("Goal achieved") == 1


@pytest.mark.asyncio
async def test_typed_goal_closure_requires_exact_declared_source_and_value():
    if not graph_transaction_available():
        pytest.skip("requires PostgreSQL evidence locks")

    from app.agentive.services.routine_task_watch import read_task_watch_snapshot
    from app.models.edges import IS_OF_TYPE
    from app.models.nodes import EntryType
    from app.services.app_graph import ensure_track_attached_operational_model

    world = await _world()
    goal, source = world["ada"], world["invoice"]

    async def publish_type(entry, schema):
        track = await Track.find_one({"id": entry.track_id})
        profile = await ensure_track_attached_operational_model(track)
        entry_type = await EntryType.create(
            track_id=track.id, name="Fixture type", form_schema=schema
        )
        await profile.connect(entry_type, edge=CONTAINS)
        entry.type_id = entry_type.id
        await entry.save()
        await entry.connect(entry_type, edge=IS_OF_TYPE)

    await publish_type(
        goal,
        {
            "fields": [
                {
                    "id": "approval-source",
                    "key": "approval_record",
                    "type": "relation",
                    "relation": {"target": "entry"},
                }
            ],
            "goal_semantics": {
                "version": 1,
                "outcome": {"source": "title"},
                "criteria": [
                    {
                        "key": "approval",
                        "evidence_relation_field_id": "approval-source",
                        "predicate": {
                            "kind": "equals",
                            "field_id": "approval",
                            "value": True,
                        },
                    },
                    {
                        "key": "count",
                        "evidence_relation_field_id": "approval-source",
                        "predicate": {"kind": "recorded_count", "field_id": "count"},
                    },
                ],
            },
        },
    )
    await publish_type(
        source,
        {
            "fields": [
                {"id": "approval", "key": "renamed_approval", "type": "boolean"},
                {"id": "count", "key": "final_count", "type": "number"},
            ]
        },
    )
    goal.custom_fields["approval_record"] = source.id
    await goal.save()
    source.custom_fields.update(renamed_approval="true", final_count=0)
    await source.save()
    await goal.connect(source, edge=REFERENCES, field_key="approval_record")
    payload = _base(
        goal,
        mode="append",
        append_field="body",
        text="Reviewed closure",
        close_goal=True,
        evidence_revisions={source.id: source.record_revision},
        idempotency_key="typed-goal-closure",
    )
    user_id = world["user"].id
    workspace_id = world["workspace"].id

    async def watch():
        return await read_task_watch_snapshot(user_id, workspace_id, goal.id, goal=True)

    initial = await watch()
    assert initial is not None and initial[1].startswith("goal:")
    source.body = "Unrelated body edit"
    source.record_revision += 1
    await source.save()
    assert await watch() == initial
    payload["evidence_revisions"][source.id] = source.record_revision

    assert (await apply_filing_facet(user_id, payload))[
        "error_code"
    ] == "goal_criteria_unmet"
    assert (await Entry.find_one({"id": goal.id})).status == "active"

    source.custom_fields.update(renamed_approval=True, final_count=-1)
    source.record_revision += 1
    await source.save()
    payload["evidence_revisions"][source.id] = source.record_revision
    assert (await apply_filing_facet(user_id, payload))[
        "error_code"
    ] == "goal_criteria_unmet"
    source.custom_fields["final_count"] = 0
    source.record_revision += 1
    await source.save()
    payload["evidence_revisions"][source.id] = source.record_revision
    ready = await watch()
    assert ready is not None and ready != initial
    assert (await apply_filing_facet(user_id, payload))["filed"] is True
    assert (await apply_filing_facet(user_id, payload))["retry"] is True
    fresh = await Entry.find_one({"id": goal.id})
    assert fresh.status == "completed"
    assert await watch() != ready
    assert fresh.body.count("Reviewed closure") == 1
