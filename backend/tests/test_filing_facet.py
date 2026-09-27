"""W2.2: an existing person is updated or linked, not duplicated."""

from __future__ import annotations

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
