"""Native screen awareness is an on-demand brokered read, not prompt data."""

from types import SimpleNamespace

import pytest
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.step_persistence import InMemoryStepStore

from app.agentive.harness.broker_tools import build_brokered_tools
from app.agentive.harness.disclosure import public_disclosure_response
from app.agentive.harness.runtime import build_native_runtime
from app.schemas.capability_broker import CapabilityResult
from app.services.chat_page_context import (
    get_page_context_for_dispatch,
    page_context_availability_hint,
    sanitize_page_snapshot,
    verify_page_snapshot,
)
from app.services.chat_providers.base import ChatTurnContext
from app.services.chat_providers.pydantic_ai_provider import _bind_integral_turn_context
from tests.native_harness.wp_06.test_provider_stream import _scope

pytestmark = pytest.mark.smoke


def _turn(snapshot=None):
    return ChatTurnContext(
        user_id="user-a",
        user_email="user@example.test",
        text="What page is this",
        thread_id="thread-a",
        workspace_id="workspace-a",
        session_id="session-a",
        focused_space_id="n.WorkspaceApp.marketing",
        extra_data={
            "run_id": "route-run",
            **({"page_context": snapshot} if snapshot is not None else {}),
        },
    )


def test_hint_contains_no_ui_payload_and_contextual_help_is_not_intercepted():
    """Only advertise availability; current-page questions stay on the capability path."""
    hint = page_context_availability_hint(
        {"url": "/private-canary", "metadata": {"secret": "private-canary"}}
    )
    assert "private-canary" not in hint
    assert "integral_get_page_context" in hint
    assert (
        public_disclosure_response("Explain what Integral can do on this page") is None
    )
    assert (
        public_disclosure_response("Explain your permission rules for this Track")
        is None
    )


@pytest.mark.asyncio
async def test_omitted_current_snapshot_cannot_fall_back_to_last_page(monkeypatch):
    """An accepted turn with no UI state cannot inherit a different tab's old page."""

    async def forbidden(_identifier):
        raise AssertionError("mutable historical thread context consulted")

    monkeypatch.setattr("app.models.nodes.ChatThread.get", forbidden)
    with _bind_integral_turn_context(_turn()):
        result = await get_page_context_for_dispatch(user_id="user-a")
    assert result["freshness"] == "absent"
    assert result["snapshot_run_id"] == "route-run"


@pytest.mark.parametrize(
    "bad",
    [
        {"url": "/apps/n.WorkspaceApp.a", "route_path": "/apps/n.WorkspaceApp.b"},
        {
            "url": "/apps/n.WorkspaceApp.a",
            "route_path": "/apps/n.WorkspaceApp.a",
            "focused_app_id": "n.WorkspaceApp.b",
        },
        {
            "url": "/tracks/n.Track.a?view_id=n.View.a",
            "route_path": "/tracks/n.Track.a",
            "focused_view_id": "n.View.b",
        },
    ],
)
def test_inconsistent_route_or_selection_is_rejected(bad):
    """Conflicting client pointers cannot define a silent new operational target."""
    with pytest.raises(ValueError):
        sanitize_page_snapshot(bad)


def test_snapshot_sanitization_caps_rows_and_removes_secret_parameters():
    """The public capability returns bounded UI data without credentials or arbitrary metadata."""
    value = sanitize_page_snapshot(
        {
            "url": "https://example.test/feed?token=SECRET&view_id=n.View.1#SECRET",
            "route_path": "/feed",
            "metadata": {"api_key": "SECRET", "view_name": "Board"},
            "visible_data": {"entries": [{"id": f"n.Entry.{i}"} for i in range(20)]},
        }
    )
    assert "SECRET" not in str(value)
    assert len(value["visible_data"]["entries"]) == 8
    assert value["visible_data"]["total_count"] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", [None, "foreign", "revoked", "missing"])
async def test_resource_metadata_is_authorized_before_hydration(monkeypatch, denial):
    """Foreign, revoked and deleted resources supply no private titles."""
    hydrated = []

    async def location(*_args: object, **_kwargs):
        return (
            None
            if denial == "missing"
            else SimpleNamespace(
                workspace_id="foreign" if denial == "foreign" else "workspace-a",
                app_id="n.WorkspaceApp.a",
                track_id="",
            )
        )

    async def role(*_args: object):
        return None if denial == "revoked" else "viewer"

    async def node(identifier):
        hydrated.append(identifier)
        return SimpleNamespace(id=identifier, name="Verified App")

    monkeypatch.setattr("app.services.resource_location.locate_resource", location)
    monkeypatch.setattr("app.services.permissions.resolve_role", role)
    monkeypatch.setattr("app.models.nodes.App.get", node)
    snapshot = {
        "focused_app_id": "n.WorkspaceApp.a",
        "metadata": {"app_name": "Forged name"},
    }
    result = await verify_page_snapshot(
        snapshot, user_id="user-a", workspace_id="workspace-a"
    )
    if denial:
        assert result is None
        assert not hydrated
    else:
        assert result["metadata"]["app_name"] == "Verified App"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question", ["What page is this", "please add dummy data to this app"]
)
async def test_page_capability_is_available_before_discovery_and_uses_bound_scope(
    tmp_path, monkeypatch, question
):
    """Orientation stays scoped and cannot bypass subsequent workflow discovery."""
    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        assert kwargs["principal_id"] == "user-a"
        assert kwargs["workspace_id"] == "workspace-a"
        return CapabilityResult(
            ok=True,
            data={
                "source": "page_context_snapshot",
                "freshness": "current_turn",
                "page_context": {
                    "page_kind": "app_detail",
                    "metadata": {"app_name": "Marketing"},
                },
            },
        )

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    catalogue = [
        {
            "name": "integral_get_page_context",
            "description": "Read the accepted UI snapshot",
            "input_schema": {
                "type": "object",
                "properties": {"include": {"type": "string"}},
            },
        }
    ]
    tools = build_brokered_tools(
        scope=_scope("route-model"), catalogue=catalogue, skill_library=tmp_path
    )
    assert not next(
        t for t in tools if t.name == "integral_get_page_context"
    ).defer_loading
    requests = []

    def model(messages, info):
        requests.append(messages)
        if len(requests) == 1:
            assert "integral_get_page_context" in {t.name for t in info.function_tools}
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "integral_get_page_context", {"include": "stub"}, "read-page"
                    )
                ]
            )
        if len(requests) == 2:
            # Discovery is still required after orientation, even though the
            # current page can already be described. No mutation is forced.
            return ModelResponse(
                parts=[ToolCallPart("search_capabilities", {"query": "current page"})]
            )
        assert any(
            isinstance(p, ToolReturnPart) and "Marketing" in str(p.content)
            for m in messages
            if isinstance(m, ModelRequest)
            for p in m.parts
        )
        return ModelResponse(
            parts=[
                TextPart(
                    "The reported page is the Marketing App. No records have been changed."
                )
            ]
        )

    agent, _store = build_native_runtime(
        model=FunctionModel(model),
        instructions=page_context_availability_hint({}),
        tools=tools,
        step_store_backend=InMemoryStepStore(),
        scope=_scope("route-model"),
        agent_name="integral_core",
    )
    result = await agent.run(
        question,
        run_id=_scope("route-model").framework_run_id,
        conversation_id=_scope("route-model").framework_conversation_id,
    )
    assert "Marketing App" in result.output
    assert [call["capability_key"] for call in calls] == ["integral_get_page_context"]


@pytest.mark.asyncio
async def test_page_schema_exposure_alone_does_not_admit_operational_prose(tmp_path):
    """Exposing the UI capability cannot satisfy operational evidence/discovery by itself."""
    tools = build_brokered_tools(
        scope=_scope("no-read"),
        skill_library=tmp_path,
        catalogue=[
            {
                "name": "integral_get_page_context",
                "description": "UI snapshot",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )

    def model(_messages, _info):
        return ModelResponse(
            parts=[TextPart("Every private record in the workspace belongs to you.")]
        )

    agent, _store = build_native_runtime(
        model=FunctionModel(model),
        instructions="",
        tools=tools,
        step_store_backend=InMemoryStepStore(),
        scope=_scope("no-read"),
        agent_name="integral_core",
    )
    with pytest.raises(UnexpectedModelBehavior):
        await agent.run(
            "Describe all private records",
            run_id=_scope("no-read").framework_run_id,
            conversation_id=_scope("no-read").framework_conversation_id,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("view_type", ["kanban", "table", "calendar", "gallery"])
async def test_selected_view_identity_uses_current_parent_acl(monkeypatch, view_type):
    """Selected View metadata is canonical and requires access to its actual Track."""

    async def locate(*_args: object, **_kwargs):
        return SimpleNamespace(
            workspace_id="workspace-a", app_id="n.WorkspaceApp.a", track_id="n.Track.t"
        )

    async def role(user, kind, identifier):
        assert (user, kind, identifier) == ("user-a", "track", "n.Track.t")
        return "viewer"

    async def node(identifier):
        return SimpleNamespace(
            id=identifier, name="Verified View", title="Verified Track", type=view_type
        )

    monkeypatch.setattr("app.services.resource_location.locate_resource", locate)
    monkeypatch.setattr("app.services.permissions.resolve_role", role)
    monkeypatch.setattr("app.models.nodes.Track.get", node)
    monkeypatch.setattr("app.models.nodes.View.get", node)
    result = await verify_page_snapshot(
        {
            "focused_track_id": "n.Track.t",
            "focused_view_id": "n.View.v",
            "metadata": {"view_name": "Forged title", "view_type": "forged type"},
        },
        user_id="user-a",
        workspace_id="workspace-a",
    )
    assert result["metadata"]["view_name"] == "Verified View"
    assert result["metadata"]["view_type"] == view_type


@pytest.mark.asyncio
async def test_stub_does_not_resolve_visible_rows_or_mutate_accepted_snapshot(
    monkeypatch,
):
    """Orientation excludes visible rows until the caller explicitly requests them."""
    snapshot = {
        "url": "/apps/n.WorkspaceApp.marketing",
        "route_path": "/apps/n.WorkspaceApp.marketing",
        "visible_data": {"entries": [{"id": "n.Entry.a", "title": "Client row"}]},
    }

    async def verify(value, **_kwargs):
        assert "visible_data" not in value
        return value

    monkeypatch.setattr("app.services.chat_page_context.verify_page_snapshot", verify)
    with _bind_integral_turn_context(_turn(snapshot)):
        result = await get_page_context_for_dispatch(user_id="user-a", include="stub")
    assert "visible_data" not in result["page_context"]
    assert snapshot["visible_data"]["entries"][0]["title"] == "Client row"


@pytest.mark.asyncio
async def test_context_vars_isolate_simultaneous_tabs(monkeypatch):
    """Two immutable accepted turns cannot replace each other's UI snapshot."""
    import asyncio

    async def verify(value, **_kwargs):
        await asyncio.sleep(0)
        return value

    monkeypatch.setattr("app.services.chat_page_context.verify_page_snapshot", verify)

    async def read(app):
        snapshot = {"url": f"/apps/{app}", "route_path": f"/apps/{app}"}
        with _bind_integral_turn_context(_turn(snapshot)):
            await asyncio.sleep(0)
            return (
                await get_page_context_for_dispatch(user_id="user-a", include="stub")
            )["page_context"]["focused_app_id"]

    assert await asyncio.gather(
        read("n.WorkspaceApp.marketing"), read("n.WorkspaceApp.crm")
    ) == ["n.WorkspaceApp.marketing", "n.WorkspaceApp.crm"]


def test_implicit_focus_uses_current_snapshot_and_clears_conflicting_state():
    """Outdated top-level focus cannot redirect an operation on the new route."""
    from app.services.agent_scope import current_focused_app_id, current_focused_view_id

    good = {"url": "/apps/n.WorkspaceApp.crm", "route_path": "/apps/n.WorkspaceApp.crm"}
    with _bind_integral_turn_context(_turn(good)):
        assert current_focused_app_id.get() == "n.WorkspaceApp.crm"
        assert current_focused_view_id.get() is None
    bad = {**good, "focused_app_id": "n.WorkspaceApp.marketing"}
    with _bind_integral_turn_context(_turn(bad)):
        assert current_focused_app_id.get() is None
