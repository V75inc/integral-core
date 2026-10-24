"""Paged editor browsing retains inventory authority without hydrating every file."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.services import agent_skills as service
from app.schemas.agentive.agent_skills import SkillBrowseQuery

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


def row(i, source="workspace"):
    return {
        "id": f"skill-{i:03}",
        "name": f"Skill {i:03}",
        "key": f"skill_{i}",
        "source": source,
        "description": "Saved description",
        "app_id": "",
        "enabled": True,
        "read_only": False,
        "kind": "declarative",
    }


async def test_page_search_filter_and_stable_order(monkeypatch):
    inventory = [row(i) for i in reversed(range(25))]
    inventory += [dict(row(30, "app"), app_id="app-a", app_name="Operations")]
    overlay = AsyncMock(return_value=inventory)
    monkeypatch.setattr(service, "list_workspace_skills", overlay)
    monkeypatch.setattr(
        service, "list_core_skills", lambda: [row(0, "core") | {"id": "core:0"}]
    )
    result = await service.browse_skills(
        "workspace", user_id="caller", query=SkillBrowseQuery(page=2)
    )
    assert result["total"] == 27
    assert len(result["skills"]) == 10
    assert result["skills"][0]["name"] == "Skill 009"
    assert result["total_pages"] == 3
    overlay.assert_awaited_with("workspace", user_id="caller", include_metadata=False)
    filtered = await service.browse_skills(
        "workspace",
        user_id="caller",
        query=SkillBrowseQuery(search="OPERATIONS", source="app"),
    )
    assert [r["id"] for r in filtered["skills"]] == ["skill-030"]
    assert filtered["apps"] == [{"id": "app-a", "name": "Operations"}]
    unknown = await service.browse_skills(
        "workspace",
        user_id="caller",
        query=SkillBrowseQuery(app_id="private-unavailable"),
    )
    assert unknown["total"] == 0 and unknown["skills"] == []


async def test_only_visible_page_reads_instruction_metadata(monkeypatch):
    inventory = [dict(row(i, "app"), app_id="app-a", description="") for i in range(60)]
    monkeypatch.setattr(
        service, "list_workspace_skills", AsyncMock(return_value=inventory)
    )
    monkeypatch.setattr(
        service.Skill,
        "get",
        AsyncMock(side_effect=lambda identity: SimpleNamespace(id=identity)),
    )
    monkeypatch.setattr(
        service.App, "get", AsyncMock(return_value=SimpleNamespace(id="app-a"))
    )
    index = AsyncMock(return_value=("revision", {}))
    monkeypatch.setattr(
        "app.services.operational_model_library_sync.load_library_bundle_index_async",
        index,
    )
    directories = AsyncMock(return_value=None)
    monkeypatch.setattr(service, "_resolve_bundle_dir_async", directories)
    reads = []

    def read(skill, directory):
        reads.append(skill.id)
        return "On-disk description", []

    monkeypatch.setattr(service, "_read_skill_disk_metadata", read)
    result = await service.browse_skills(
        "workspace", user_id="caller", query=SkillBrowseQuery(source="app", page=3)
    )
    assert result["total"] == 60
    assert reads == [f"skill-{i:03}" for i in range(20, 30)]
    index.assert_awaited_once()
    directories.assert_awaited_once()


async def test_core_browse_does_not_build_workspace_inventory(monkeypatch):
    inventory = AsyncMock(side_effect=AssertionError("Unnecessary workspace inventory"))
    monkeypatch.setattr(service, "list_workspace_skills", inventory)
    monkeypatch.setattr(service, "list_core_skills", lambda: [row(1, "core")])
    result = await service.browse_skills(
        "workspace", user_id="caller", query=SkillBrowseQuery(source="core", page=999)
    )
    assert result["page"] == 1 and result["total"] == 1
    inventory.assert_not_awaited()


async def test_browse_api_is_bounded_and_validates_parameters(
    authenticated_client, auth_token
):
    response = await authenticated_client.get(
        "/api/agentive/skills/browse?source=core&page_size=3&page=2"
    )
    assert response.status_code == 200, response.text
    page = response.json()
    assert len(page["skills"]) == 3 and page["total"] >= 12
    assert page["page"] == 2
    assert all("resolved_body" not in skill for skill in page["skills"])
    invalid = await authenticated_client.get(
        "/api/agentive/skills/browse?page_size=5000"
    )
    assert invalid.status_code in {400, 422}
