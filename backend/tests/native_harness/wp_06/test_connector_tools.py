"""Native discovery uses mounted, caller-scoped MCP declarations."""

from types import SimpleNamespace

import pytest

from app.agentive.harness import connector_tools


def _spec(key, slug=None, row_id=None, remote="search_web", **extra):
    return {
        "key": key,
        "description": "Search current public web sources.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        "_mcp_connector_slug": slug,
        "_mcp_connector_id": row_id,
        "_mcp_remote_name": remote,
        **extra,
    }


@pytest.mark.asyncio
async def test_native_connector_projection_is_scoped_redacted_and_deduplicated(
    monkeypatch,
):
    rows = [
        SimpleNamespace(
            id="mine",
            owner="user",
            connection_mode="per_user",
            slug="serper_web_search",
        ),
        SimpleNamespace(
            id="other", owner="teammate", connection_mode="per_user", slug="private"
        ),
        SimpleNamespace(
            id="shared", owner="admin", connection_mode="shared", slug="custom"
        ),
    ]

    async def get_rows(workspace):
        assert workspace == "workspace"
        return rows

    monkeypatch.setattr(connector_tools, "rows_for_workspace", get_rows)
    monkeypatch.setattr(connector_tools, "row_slug", lambda row: row.slug)
    specs = {
        "canonical": _spec(
            "mcp__serper_web_search__search_web",
            slug="serper_web_search",
            auth_state={"secret": "never expose"},
        ),
        "alias": _spec("mcp__mine__search_web", row_id="mine"),
        "private": _spec("mcp__private__search_web", slug="private"),
        "custom": _spec(
            "mcp__shared__fetch",
            row_id="shared",
            remote="fetch",
            _mcp_annotations={"readOnlyHint": True},
        ),
        "disabled": _spec(
            "mcp__serper_web_search__disabled",
            slug="serper_web_search",
            agent_callable=False,
        ),
        "app": {"key": "app_tool", "input_schema": {"type": "object"}},
    }
    monkeypatch.setattr(connector_tools, "get_workspace_tools", lambda workspace: specs)
    result = await connector_tools.build_connector_tool_catalogue(
        workspace_id="workspace", principal_id="user"
    )
    by_name = {item["name"]: item for item in result}
    assert set(by_name) == {"mcp__serper_web_search__search_web", "mcp__shared__fetch"}
    assert by_name["mcp__serper_web_search__search_web"]["op_class"] == "read"
    assert by_name["mcp__shared__fetch"]["op_class"] == "execute"
    assert "secret" not in str(result)
    assert all(
        set(item) == {"name", "description", "input_schema", "op_class"}
        for item in result
    )
    assert "mcp__serper_web_search__search_web" not in {
        item["name"]
        for item in await connector_tools.build_connector_tool_catalogue(
            workspace_id="workspace", principal_id="stranger"
        )
    }


def test_portable_resident_skill_is_available_to_native_core_discovery():
    from app.agentive.services.agent_skills import list_core_skills

    core = {row["key"]: row for row in list_core_skills()}
    assert "web-research" in core
    assert core["web-research"]["read_only"] is True
    assert (
        "mcp__serper_web_search__fetch_web_page"
        in core["web-research"]["tools_required"]
    )
    assert "search retrieval" in core["web-research"]["resolved_body"]
