"""Qualify optional native aggregates against canonical current authority."""

import os

import pytest
from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CONTAINS, EXCLUDED_FROM
from app.services.permissions import count_user_accessible_entries
from app.services.workspace_governance import ordinary_authority_only
from tests.test_resource_location import graph

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.getenv("INTEGRAL_TEST_DB") != "postgres", reason="PostgreSQL qualification"
    ),
]


@pytest.fixture
def native_database():
    database = get_default_context().database
    if not getattr(database, "supports_native_graph_binding_summary", False):
        if os.getenv("INTEGRAL_REQUIRE_NATIVE_BINDING_SUMMARY") == "1":
            pytest.fail(
                "The qualified candidate must provide native graph binding summaries"
            )
        pytest.skip("Optional framework optimization is not installed")
    return database


async def test_native_count_executes_and_rechecks_rooted_owner(
    native_database, monkeypatch
):
    owner, _, _, track, _ = await graph()
    original = native_database.summarize_graph_bindings
    called = []

    async def observed(*args, **kwargs):
        called.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(native_database, "summarize_graph_bindings", observed)
    assert await count_user_accessible_entries(owner.id, track.id) == 1
    assert len(called) == 1


@pytest.mark.parametrize(
    "damage",
    [
        "visibility_null",
        "visibility_unknown",
        "legacy_conflict",
        "missing_parent",
        "extra_parent",
        "payload_only",
    ],
)
async def test_native_count_matches_raw_integrity_contract(native_database, damage):
    owner, _, _, track, entry = await graph()
    record = (await native_database.find("node", {"id": entry.id}))[0]
    if damage == "visibility_null":
        record["context"]["visibility"] = None
    elif damage == "visibility_unknown":
        record["context"]["visibility"] = "unknown"
    elif damage == "legacy_conflict":
        record["track_id"] = "n.Track.inconsistent"
    elif damage == "payload_only":
        record["context"]["title"] = None
    if damage == "missing_parent":
        edge = (
            await native_database.find(
                "edge", {"source": track.id, "target": entry.id, "entity": "CONTAINS"}
            )
        )[0]
        await native_database.delete("edge", edge["id"])
    elif damage == "extra_parent":
        _, _, _, foreign_track, _ = await graph()
        await foreign_track.connect(entry, edge=CONTAINS)
    else:
        await native_database.save("node", record)
    if damage in ("visibility_null", "visibility_unknown", "legacy_conflict"):
        with pytest.raises(AuthorizationUnavailableError):
            await count_user_accessible_entries(owner.id, track.id)
    else:
        assert await count_user_accessible_entries(owner.id, track.id) == (
            1 if damage == "payload_only" else 0
        )


async def test_ordinary_authority_keeps_entry_exclusion_and_never_uses_owner_aggregate(
    native_database, monkeypatch
):
    owner, _, _, track, entry = await graph()
    await owner.connect(entry, edge=EXCLUDED_FROM)

    async def forbidden(*args, **kwargs):
        pytest.fail("Ordinary authority must not use the protected-owner aggregate")

    monkeypatch.setattr(native_database, "summarize_graph_bindings", forbidden)
    with ordinary_authority_only():
        assert await count_user_accessible_entries(owner.id, track.id) == 0


async def test_native_source_failure_is_not_zero(native_database, monkeypatch):
    owner, _, _, track, _ = await graph()

    async def unavailable(*args, **kwargs):
        raise RuntimeError("native source failed")

    monkeypatch.setattr(native_database, "summarize_graph_bindings", unavailable)
    with pytest.raises(AuthorizationUnavailableError):
        await count_user_accessible_entries(owner.id, track.id)


async def test_ownership_changed_during_aggregate_cannot_return_a_total(
    native_database, monkeypatch
):
    from app.models.edges import IS_MEMBER_OF

    owner, workspace, _, track, _ = await graph()
    original = native_database.summarize_graph_bindings

    async def revoke_after_read(*args, **kwargs):
        result = await original(*args, **kwargs)
        edge = (
            await native_database.find(
                "edge",
                {
                    "source": owner.id,
                    "target": workspace.id,
                    "entity": IS_MEMBER_OF._entity_name(),
                },
            )
        )[0]
        edge["context"]["role"] = "member"
        await native_database.save("edge", edge)
        return result

    monkeypatch.setattr(native_database, "summarize_graph_bindings", revoke_after_read)
    with pytest.raises(AuthorizationUnavailableError):
        await count_user_accessible_entries(owner.id, track.id)


@pytest.mark.parametrize(
    "query,title,body",
    [
        ("kelvin", "\u212aelvin", ""),
        ("hi", "h\u0130", ""),
        ("id", "\u0130d", ""),
        ("budget OR invoice", "Unrelated", "INVOICE"),
        ("and or the", "No terms", ""),
        ("niño", "NIÑO", ""),
    ],
)
async def test_keyword_prefilter_preserves_canonical_matches_on_postgres(
    native_database, query, title, body
):
    from app.services.agent_insights import query_entries
    from app.services.retrieval.keyword_match import matches_keywords, tokenize

    owner, _, _, track, entry = await graph()
    entry.title = title
    entry.body = body
    await entry.save()
    result = await query_entries(
        user_id=owner.id, track_id=track.id, query=query, limit=20
    )
    expected = (
        [entry.id] if matches_keywords(title + " " + body, tokenize(query)) else []
    )
    assert [row["id"] for row in result["entries"]] == expected
    assert result["total"] == len(expected)


async def test_incidental_catalog_edge_is_not_an_entry_authority_fact(
    native_database, monkeypatch
):
    from unittest.mock import AsyncMock

    owner, _, _, track, entry = await graph()
    await native_database.save(
        "edge",
        {
            "id": "e.CATALOGS.incidental",
            "entity": "CATALOGS",
            "source": None,
            "target": entry.id,
            "context": {},
        },
    )
    assert await count_user_accessible_entries(owner.id, track.id) == 1
    monkeypatch.setattr(
        native_database, "summarize_graph_bindings", AsyncMock(return_value=None)
    )
    assert await count_user_accessible_entries(owner.id, track.id) == 1
