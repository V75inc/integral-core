"""Typed relation writers keep targets inside the source workspace.

These are storage and shared-writer regressions, not proof of target read
admission, canonical App authority, transport, or cross-worker revocation.
"""

import pytest

from app.exceptions import BadRequestError
from app.models.edges import COLLABORATES_ON, CONTAINS, OWNS, REFERENCES
from app.models.nodes import Entry, EntryType, Track
from app.services.operational_model_entry_fields import _validate_relation_values

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def _fixture(
    *, many=False, target_workspace="relation-other-workspace", writer=None
):
    workspace_id = "relation-source-workspace"
    writer_id = "relation-editor"
    if writer is not None:
        from app.services.personal_workspace import ensure_personal_workspace

        workspace = await ensure_personal_workspace(writer)
        workspace_id = workspace.id
        writer_id = writer.user_id
    source = await Track.create(
        title="Source",
        owner_id=writer_id,
        workspace_id=workspace_id,
    )
    local_track = await Track.create(
        title="Local targets",
        owner_id="relation-owner",
        workspace_id=source.workspace_id,
    )
    foreign_track = await Track.create(
        title="Other targets", owner_id="relation-owner", workspace_id=target_workspace
    )
    target_type = await EntryType.create(
        name="Record",
        name_fold="record",
        track_id=local_track.id,
        form_schema={"_manifest_entry_type_key": "record", "fields": []},
    )
    local = await Entry.create(
        title="Local",
        track_id=local_track.id,
        type_id=target_type.id,
        author_id="relation-owner",
    )
    foreign = await Entry.create(
        title="Other",
        track_id=foreign_track.id,
        type_id=target_type.id,
        author_id="relation-owner",
    )
    await local_track.connect(local, edge=CONTAINS)
    if writer is not None:
        await writer.connect(source, edge=OWNS)
        await writer.connect(local, edge=COLLABORATES_ON, role="viewer")
    await foreign_track.connect(foreign, edge=CONTAINS)
    source_type = await EntryType.create(
        name="Source Record",
        name_fold="source record",
        track_id=source.id,
        form_schema={
            "fields": [
                {
                    "key": "related",
                    "type": "relation",
                    "relation": {
                        "target": "entry",
                        "many": many,
                        "allow_cross_track": True,
                        "target_entry_types": ["record"],
                    },
                }
            ]
        },
    )
    return source, source_type, local, foreign, foreign_track


@pytest.mark.parametrize("many", [False, True])
async def test_update_refuses_foreign_workspace_without_changing_record_or_edges(
    many, test_user
):
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_update import update_entry_in_track

    source, entry_type, local, foreign, _ = await _fixture(many=many, writer=test_user)
    initial = [local.id] if many else local.id
    entry = await create_entry_in_track(
        track=source,
        entry_type=entry_type,
        title="Retained record",
        user_id=test_user.user_id,
        workspace_id=source.workspace_id,
        custom_fields={"related": initial},
        skip_profanity=True,
    )
    graph = await entry.get_context()
    original_edges = await graph.find_edges_between(
        entry.id, None, edge_class=REFERENCES
    )
    original_revision = entry.record_revision
    proposed = [local.id, foreign.id] if many else foreign.id
    with pytest.raises(BadRequestError, match="workspace"):
        await update_entry_in_track(
            entry_id=entry.id,
            user_id=test_user.user_id,
            workspace_id=source.workspace_id,
            custom_fields={"related": proposed},
            expected_record_revision=original_revision,
        )
    await graph._evict_from_cache(entry.id)
    restored = await Entry.get(entry.id)
    assert restored.custom_fields["related"] == initial
    assert restored.record_revision == original_revision
    edges = await graph.find_edges_between(entry.id, None, edge_class=REFERENCES)
    assert {e.id for e in edges} == {e.id for e in original_edges}
    assert (
        await graph.find_edges_between(entry.id, foreign.id, edge_class=REFERENCES)
        == []
    )


async def test_create_refuses_foreign_workspace_before_creating_entry(test_user):
    from app.services.entry_create import create_entry_in_track

    source, entry_type, _, foreign, _ = await _fixture()
    before = await source.nodes(edge=[CONTAINS], node=["Entry"])
    with pytest.raises(BadRequestError, match="workspace"):
        await create_entry_in_track(
            track=source,
            entry_type=entry_type,
            title="Refused record",
            user_id=test_user.user_id,
            workspace_id=source.workspace_id,
            custom_fields={"related": foreign.id},
            skip_profanity=True,
        )
    after = await source.nodes(edge=[CONTAINS], node=["Entry"])
    assert {e.id for e in after} == {e.id for e in before}
    assert await Entry.find({"context.title": "Refused record"}) == []


async def test_same_workspace_cross_track_remains_valid():
    source, _, local, _, _ = await _fixture()
    assert await _validate_relation_values(
        relation={"target": "entry", "allow_cross_track": True},
        value=local.id,
        source_track=source,
        field_key="related",
    ) == [local.id]


@pytest.mark.parametrize(
    "missing", ["target_track", "target_workspace", "source_workspace"]
)
async def test_entry_relation_refuses_unresolved_workspace(missing):
    source, _, _, target, target_track = await _fixture(target_workspace="")
    if missing == "target_track":
        target.track_id = "n.Track.missing"
        await target.save()
    elif missing == "source_workspace":
        source.workspace_id = ""
    with pytest.raises(BadRequestError, match="workspace|Track"):
        await _validate_relation_values(
            relation={"target": "entry", "allow_cross_track": True},
            value=target.id,
            source_track=source,
            field_key="related",
        )
