"""Budget and authority regressions through the real Pydantic discovery loop."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness import Skills

from app.agentive.harness.broker_tools import build_brokered_tools
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.pydantic_ai_compat import IntegralToolDisclosure
from app.schemas.capability_broker import CapabilityResult

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
async def test_loading_lookup_skill_does_not_disclose_unsearched_mutation_schemas(
    tmp_path: Path, monkeypatch
):
    """Skill procedure and exact discovered read survive without a write catalog."""
    folder = tmp_path / "record-lookup"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: record-lookup\ndescription: Find records by exact title.\n"
        "allowed-tools: integral_query_entries integral_delete_entry\n---\n"
        "Use integral_query_entries to find records by exact title. "
        "Delete only when explicitly requested and approved.\n"
    )
    scope = HarnessExecutionScope(
        tenant_id="ws",
        principal_id="user",
        workspace_id="ws",
        thread_id="thread",
        session_id="session",
        run_id="run",
        permission_revision="acl",
        capability_version="catalog",
    )
    catalogue = [
        {
            "name": "integral_query_entries",
            "description": "Find records by exact title.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
        {
            "name": "integral_delete_entry",
            "description": "Permanently delete one entry.",
            "input_schema": {
                "type": "object",
                "properties": {"entry_id": {"type": "string"}},
            },
        },
    ]
    monkeypatch.setattr(
        "app.agentive.harness.capability_search._embed_text_for_capability_search",
        AsyncMock(side_effect=RuntimeError("lexical fixture")),
    )
    invoke = AsyncMock(return_value=CapabilityResult(ok=True, data={"entries": []}))
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tools = build_brokered_tools(
        scope=scope, catalogue=catalogue, skill_library=tmp_path
    )
    seen = []

    def respond(messages, info):
        visible = {tool.name for tool in info.function_tools}
        seen.append(visible)
        if len(seen) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_capabilities",
                        {"query": "find records by exact title", "limit": 2},
                    )
                ]
            )
        if len(seen) == 2:
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "record-lookup"})]
            )
        assert "integral_delete_entry" not in visible
        assert "integral_query_entries" in visible
        if len(seen) == 3:
            return ModelResponse(
                parts=[
                    ToolCallPart("integral_query_entries", {"query": "missing-item"})
                ]
            )
        return ModelResponse(parts=[TextPart("No matching readable record.")])

    agent = Agent(
        FunctionModel(respond),
        tools=tools,
        capabilities=[
            IntegralToolDisclosure(),
            Skills(tmp_path, include={"record-lookup"}),
        ],
    )
    result = await agent.run("Find the exact record missing-item.")
    assert result.output == "No matching readable record."
    assert len(seen) == 4
    invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_each_work_item_gets_a_fresh_permission_cache(monkeypatch):
    """Workers cannot carry a cached ACL or inventory into their next item."""
    from app.agentive.services import work_worker
    from app.agentive.work_models import WorkItem
    from app.middleware.permissions_cache import (
        permissions_cache_get,
        reset_permissions_cache,
    )

    reset_permissions_cache()
    parent = permissions_cache_get()
    parent[("rr", "app", "private-app", "user")] = "owner"
    observed = []

    async def handle(item, **kwargs):
        cache = permissions_cache_get()
        observed.append(dict(cache))
        cache[("user_node", item.principal_id)] = "first-item-state"
        return item

    monkeypatch.setattr(work_worker, "_handle_chat_turn", handle)
    try:
        for key in ("first", "second"):
            await work_worker.execute_claimed_work(
                WorkItem(work_item_id=key, principal_id="user", kind="chat_turn")
            )
        assert observed == [{}, {}]
        assert permissions_cache_get() is parent
        assert parent == {("rr", "app", "private-app", "user"): "owner"}
    finally:
        reset_permissions_cache()


@pytest.mark.asyncio
async def test_read_discovery_does_not_require_loading_an_unrelated_owner_skill(
    tmp_path, monkeypatch
):
    """Supporting reads stay callable under the live broker without another SOP."""
    folder = tmp_path / "identity-work"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: identity-work\ndescription: Manage identity and workspace.\nallowed-tools: integral_whoami\n---\nRead identity.\n"
    )
    monkeypatch.setattr(
        "app.agentive.harness.capability_search._embed_text_for_capability_search",
        AsyncMock(side_effect=RuntimeError("lexical fixture")),
    )
    invoke = AsyncMock(
        return_value=CapabilityResult(ok=True, data={"principal_id": "user"})
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    scope = HarnessExecutionScope(
        tenant_id="ws",
        principal_id="user",
        workspace_id="ws",
        thread_id="t",
        session_id="s",
        run_id="r",
        permission_revision="acl",
        capability_version="cat",
    )
    tools = build_brokered_tools(
        scope=scope,
        catalogue=[
            {
                "name": "integral_whoami",
                "description": "Read current identity",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        skill_library=tmp_path,
    )
    requests = []

    def respond(messages, info):
        requests.append({tool.name for tool in info.function_tools})
        if len(requests) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_capabilities",
                        {"query": "integral_whoami current identity", "limit": 1},
                    )
                ]
            )
        assert "integral_whoami" in requests[-1]
        if len(requests) == 2:
            return ModelResponse(parts=[ToolCallPart("integral_whoami", {})])
        return ModelResponse(parts=[TextPart("Current principal is user.")])

    agent = Agent(
        FunctionModel(respond),
        tools=tools,
        capabilities=[
            IntegralToolDisclosure(),
            Skills(tmp_path, include={"identity-work"}),
        ],
    )
    assert (
        await agent.run("Read current principal")
    ).output == "Current principal is user."
    assert len(requests) == 3
    invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_broker_calls_do_not_reuse_inherited_permissions(monkeypatch):
    from types import SimpleNamespace

    from app.middleware.permissions_cache import (
        permissions_cache_get,
        reset_permissions_cache,
    )

    reset_permissions_cache()
    parent = permissions_cache_get()
    parent[("rr", "entry", "private", "user")] = "owner"
    seen = []

    async def invoke(**kwargs):
        seen.append(dict(permissions_cache_get()))
        permissions_cache_get()[("user_node", "user")] = "cached"
        return CapabilityResult(ok=True, data={"entries": []})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    scope = HarnessExecutionScope(
        tenant_id="ws",
        principal_id="user",
        workspace_id="ws",
        thread_id="t",
        session_id="s",
        run_id="r",
        permission_revision="acl",
        capability_version="cat",
    )
    tool = build_brokered_tools(
        scope=scope,
        catalogue=[
            {
                "name": "integral_query_entries",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )[0]
    try:
        for _ in range(2):
            await tool.function(
                SimpleNamespace(active_capability_ids=frozenset(), tool_call_id="call"),
                **{},
            )
        assert seen == [{}, {}]
        assert permissions_cache_get() is parent
    finally:
        reset_permissions_cache()


def test_active_workflow_does_not_force_another_skill_for_supporting_read():
    from types import SimpleNamespace

    from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

    ctx = SimpleNamespace(
        model=SimpleNamespace(profile={}),
        active_capability_ids={"roster"},
        messages=[
            ModelRequest(parts=[UserPromptPart("Who is active?")]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        "search_capabilities",
                        {
                            "results": [
                                {
                                    "kind": "skill",
                                    "load_with": {
                                        "tool": "load_capability",
                                        "id": "identity-work",
                                    },
                                }
                            ]
                        },
                        "s",
                    )
                ]
            ),
        ],
    )
    choice = IntegralToolDisclosure(
        workflow_skill_ids=frozenset({"roster"})
    ).get_model_settings()
    assert choice(ctx).get("tool_choice") != ["load_capability"]


@pytest.mark.asyncio
async def test_permission_scope_restores_parent_after_error_and_parallel_calls():
    import asyncio

    from app.middleware.permissions_cache import (
        isolated_permissions_cache,
        permissions_cache_get,
        reset_permissions_cache,
    )

    reset_permissions_cache()
    parent = permissions_cache_get()
    parent[("user_node", "parent")] = "parent"
    with pytest.raises(RuntimeError):
        with isolated_permissions_cache():
            permissions_cache_get()[("user_node", "child")] = "child"
            raise RuntimeError("failed call")
    assert permissions_cache_get() is parent

    async def call(name):
        with isolated_permissions_cache():
            memo = permissions_cache_get()
            memo[("user_node", name)] = name
            await asyncio.sleep(0)
            return dict(memo)

    assert await asyncio.gather(call("a"), call("b")) == [
        {("user_node", "a"): "a"},
        {("user_node", "b"): "b"},
    ]
    assert permissions_cache_get() is parent
    reset_permissions_cache()


def test_live_authority_bypasses_production_ttl_cache(monkeypatch):
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.services import permissions_process_cache as cache

    monkeypatch.setattr(cache, "_ENABLED", True)
    cache.clear_all()
    cache.set_cached("user", "rr:app:private", "owner")
    try:
        assert cache.get_cached("user", "rr:app:private") == "owner"
        with isolated_permissions_cache():
            assert not cache.enabled()
            assert cache.get_cached("user", "rr:app:private") is None
            assert (
                cache.get_resolve_role_cached("user", "app", "private")
                is cache._ROLE_CACHE_MISS
            )
            cache.set_cached("user", "rr:app:private", "viewer")
        assert cache.get_cached("user", "rr:app:private") == "owner"
    finally:
        cache.clear_all()


@pytest.mark.parametrize(
    "available, expected",
    [
        (frozenset(), "search_capabilities"),
        (
            frozenset({"integral_describe_capabilities"}),
            "integral_describe_capabilities",
        ),
    ],
)
def test_refused_read_requires_the_declared_recovery_before_other_queries(
    available, expected
):
    from types import SimpleNamespace

    from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

    messages = [
        ModelRequest(parts=[UserPromptPart("Count records")]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "integral_query_entries",
                    {"error": True, "next_tool": "integral_describe_capabilities"},
                    "denied",
                )
            ]
        ),
    ]
    ctx = SimpleNamespace(
        model=SimpleNamespace(profile={}),
        active_capability_ids={"record-work"},
        available_tool_names=available,
        messages=messages,
    )
    policy = IntegralToolDisclosure(
        workflow_skill_ids=frozenset({"record-work"})
    ).get_model_settings()
    assert policy(ctx)["tool_choice"] == [expected]
    messages.append(
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "integral_describe_capabilities", {"capabilities": []}, "recovered"
                )
            ]
        )
    )
    assert policy(ctx).get("tool_choice") != ["integral_describe_capabilities"]


@pytest.mark.asyncio
async def test_declared_app_read_is_allowed_under_no_write_request(monkeypatch):
    from types import SimpleNamespace

    from app.agentive.tooling.catalogue import build_tool_catalogue

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.is_declared_app_read",
        AsyncMock(return_value=True),
    )
    invoke = AsyncMock(return_value=CapabilityResult(ok=True, data={"count": 3}))
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    scope = HarnessExecutionScope(
        tenant_id="ws",
        principal_id="user",
        workspace_id="ws",
        thread_id="t",
        session_id="s",
        run_id="r",
        permission_revision="acl",
        capability_version="cat",
    )
    catalogue = [
        item
        for item in build_tool_catalogue()
        if item["name"] == "integral_invoke_app_operation"
    ]
    tool = build_brokered_tools(
        scope=scope, catalogue=catalogue, no_workspace_writes=True
    )[0]
    ctx = SimpleNamespace(active_capability_ids=frozenset(), tool_call_id="read")
    result = await tool.function(
        ctx, app_id="app", operation_key="read_roster", input={}
    )
    assert result["count"] == 3
    invoke.assert_awaited_once()
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.is_declared_app_read",
        AsyncMock(return_value=False),
    )
    result = await tool.function(ctx, app_id="app", operation_key="pay", input={})
    assert result["error_code"] == "user_no_workspace_writes"
    invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_turn_projection_does_not_inherit_permission_state():
    from types import SimpleNamespace

    from app.middleware.permissions_cache import (
        permissions_cache_get,
        reset_permissions_cache,
    )
    from app.services import permissions_process_cache as cache
    from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider

    reset_permissions_cache()
    parent = permissions_cache_get()
    parent[("user_node", "prior")] = "prior"

    async def prepare(_ctx):
        assert permissions_cache_get() == {}
        assert not cache.enabled()
        return "fresh authorized projection"

    try:
        result = await PydanticAIProvider._prepare(
            SimpleNamespace(_prepare_current=prepare), SimpleNamespace()
        )
        assert result == "fresh authorized projection"
        assert permissions_cache_get() is parent
    finally:
        reset_permissions_cache()
