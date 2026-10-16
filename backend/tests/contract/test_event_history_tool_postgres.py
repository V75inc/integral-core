"""Native audit tools retain request scope and the declared forensic filters."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive.tooling.dispatch import dispatch_tool
from app.models.edges import CONTAINS, IS_MEMBER_OF
from app.models.nodes import Entry, User
from app.services.change_event_logger import ChangeEventEnvelope, ChangeEventLogger
from tests.contract.test_release_admission_postgres import remove_membership
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def audit_fixture(monkeypatch, database):
    from app.api import audit_log

    auth, workspace, track, _ = await proposal()
    _, other_workspace, other_track, _ = await proposal()
    profile = (await User.find({"user_id": auth.id}))[0]
    await profile.connect(other_workspace, edge=IS_MEMBER_OF, role="owner")
    entry = await Entry.create(title="Audit detail", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    logger = ChangeEventLogger()
    context = GraphContext(database=database)
    monkeypatch.setattr(logger, "_get_log_context", lambda: context)
    monkeypatch.setattr(audit_log, "get_change_event_logger", lambda: logger)
    identifiers = []
    for resource, resource_type, parent, actor_kind in (
        (track, "Track", track, "human"),
        (entry, "Entry", track, "agent"),
        (other_track, "Track", other_track, "human"),
    ):
        event = ChangeEventEnvelope(
            id="",
            ts="2026-10-16T00:00:00+00:00",
            actor_kind=actor_kind,
            actor_id=auth.id,
            action=f"{resource_type.lower()}.update",
            resource_type=resource_type,
            resource_id=resource.id,
            scope=f"track:{parent.id}",
            before=None,
            after={"id": resource.id},
        )
        await logger.persist(event)
        assert event.id
        identifiers.append(event.id)
    return auth, workspace, other_workspace, track, entry, identifiers


@pytest.mark.parametrize(
    "filter_name", ["resource_id", "resource_type", "actor_kind", "action"]
)
async def test_native_audit_tool_applies_declared_resource_filters(
    postgres_raw_db, monkeypatch, filter_name
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, track, _, identifiers = await audit_fixture(
            monkeypatch, postgres_raw_db
        )
        from app.agentive.tooling.catalogue import build_tool_catalogue

        tool = next(
            tool
            for tool in build_tool_catalogue()
            if tool["name"] == "integral_query_audit_log"
        )
        assert filter_name in tool["input_schema"]["properties"]
        filters = {
            "resource_id": track.id,
            "resource_type": "Track",
            "actor_kind": "human",
            "action": "track.update",
        }
        result = await dispatch_tool(
            "integral_query_audit_log",
            {filter_name: filters[filter_name]},
            principal_id=auth.id,
            scope=workspace.id,
        )
        assert not result.is_error, result
        assert [event["id"] for event in result.data["events"]] == identifiers[:1]


@pytest.mark.parametrize("invalid_scope", ["revoked", "unknown"])
async def test_native_audit_tool_refuses_invalid_bound_workspace(
    postgres_raw_db, monkeypatch, invalid_scope
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, _, _, identifiers = await audit_fixture(
            monkeypatch, postgres_raw_db
        )
        bound = workspace.id
        if invalid_scope == "revoked":
            await remove_membership(auth, workspace)
        else:
            bound = "n.Workspace.unknown"
        result = await dispatch_tool(
            "integral_query_audit_log", {}, principal_id=auth.id, scope=bound
        )
        assert result.is_error, result
        assert result.data is None
        assert all(identifier not in result.message for identifier in identifiers)


async def test_native_audit_continuation_cannot_cross_bound_workspaces(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, other, _, _, identifiers = await audit_fixture(
            monkeypatch, postgres_raw_db
        )
        first = await dispatch_tool(
            "integral_query_audit_log",
            {"limit": 1},
            principal_id=auth.id,
            scope=workspace.id,
        )
        assert not first.is_error, first
        from app.agentive.tooling.catalogue import build_tool_catalogue

        tool = next(
            tool
            for tool in build_tool_catalogue()
            if tool["name"] == "integral_query_audit_log"
        )
        assert "cursor" in tool["input_schema"]["properties"]
        cursor = first.data["next_cursor"]
        assert cursor
        next_page = await dispatch_tool(
            "integral_query_audit_log",
            {"cursor": cursor, "limit": 1},
            principal_id=auth.id,
            scope=workspace.id,
        )
        assert not next_page.is_error, next_page
        first_ids = {event["id"] for event in first.data["events"]}
        next_ids = {event["id"] for event in next_page.data["events"]}
        assert first_ids | next_ids == set(identifiers[:2])
        assert first_ids.isdisjoint(next_ids)
        crossed = await dispatch_tool(
            "integral_query_audit_log",
            {"cursor": cursor, "limit": 1},
            principal_id=auth.id,
            scope=other.id,
        )
        assert crossed.is_error, crossed
        assert crossed.data is None
        assert all(identifier not in crossed.message for identifier in identifiers)


async def test_native_audit_account_events_stay_in_personal_workspace(
    postgres_raw_db, monkeypatch
):
    from app.api import audit_log
    from app.services.personal_workspace import ensure_personal_workspace

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, _, _, _ = await audit_fixture(monkeypatch, postgres_raw_db)
        profile = (await User.find({"user_id": auth.id}))[0]
        personal = await ensure_personal_workspace(profile)
        event = ChangeEventEnvelope(
            id="",
            ts="2026-10-16T00:00:00+00:00",
            actor_kind="human",
            actor_id=auth.id,
            action="user.update",
            resource_type="User",
            resource_id=profile.id,
            scope=f"user:{profile.id}",
            before=None,
            after={"id": profile.id, "display_name": "Own account event"},
        )
        await audit_log.get_change_event_logger().persist(event)
        assert event.id
        for bound, expected in ((personal.id, [event.id]), (workspace.id, [])):
            result = await dispatch_tool(
                "integral_query_audit_log",
                {"resource_id": profile.id},
                principal_id=auth.id,
                scope=bound,
            )
            assert not result.is_error, result
            assert [item["id"] for item in result.data["events"]] == expected
