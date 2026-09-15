"""Integration tests for the draft / publish / diff / discard lifecycle."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestContentProfileDrafts:
    async def _create_published_library_cp(
        self, authenticated_client: AsyncClient
    ) -> dict:
        org_resp = await authenticated_client.post(
            "/api/workspaces", json={"name": "Drafts Org"}
        )
        assert org_resp.status_code == 200
        org_id = org_resp.json()["workspace"]["id"]
        manifest = {
            "content_profile_schema_version": 2,
            "scope": "track",
            "package": {"name": "drafts-test"},
            "track": {
                "entry_types": [{"key": "task", "name": "Task", "fields": []}],
                "views": [{"key": "feed", "name": "Feed", "view_type": "feed"}],
                "taxonomy": {"tag_groups": []},
            },
        }
        pub = await authenticated_client.post(
            "/api/content-profiles",
            json={
                "name": "Drafts Test Pack",
                "workspace_id": org_id,
                "manifest": manifest,
            },
        )
        assert pub.status_code == 200, pub.text
        return pub.json()["content_profile"]

    async def test_fork_draft_creates_sibling(
        self, authenticated_client: AsyncClient, test_user
    ):
        published = await self._create_published_library_cp(authenticated_client)
        fork = await authenticated_client.post(
            f"/api/content-profiles/{published['id']}/draft", json={}
        )
        assert fork.status_code == 200, fork.text
        body = fork.json()
        assert body["from_id"] == published["id"]
        draft = body["draft"]
        assert draft["status"] == "draft"
        assert draft["draft_of_id"] == published["id"]
        # Manifest copied from parent
        assert draft["manifest"]["track"]["entry_types"][0]["key"] == "task"

    async def test_publish_swaps_manifest_and_bumps_version(
        self, authenticated_client: AsyncClient, test_user
    ):
        published = await self._create_published_library_cp(authenticated_client)
        published_id = published["id"]
        original_version = int(published.get("version_number", 1))

        fork = await authenticated_client.post(
            f"/api/content-profiles/{published_id}/draft", json={}
        )
        draft = fork.json()["draft"]
        # Mutate the draft via the existing PUT endpoint
        new_manifest = dict(draft["manifest"])
        new_manifest["track"]["entry_types"].append(
            {"key": "note", "name": "Note", "fields": []}
        )
        upd = await authenticated_client.put(
            f"/api/content-profiles/{draft['id']}",
            json={"manifest": new_manifest},
        )
        assert upd.status_code == 200, upd.text

        publish = await authenticated_client.post(
            f"/api/content-profiles/{draft['id']}/publish",
            json={"run_migrations": False},
        )
        assert publish.status_code == 200, publish.text
        body = publish.json()
        assert body["published_id"] == published_id
        assert body["version_number"] == original_version + 1

        # Reload published parent and confirm new entry type landed
        re_get = await authenticated_client.get(f"/api/content-profiles/{published_id}")
        assert re_get.status_code == 200
        manifest = re_get.json()["content_profile"]["manifest"]
        keys = [et["key"] for et in manifest["track"]["entry_types"]]
        assert "note" in keys

    async def test_diff_returns_structural_delta(
        self, authenticated_client: AsyncClient, test_user
    ):
        published = await self._create_published_library_cp(authenticated_client)
        fork = await authenticated_client.post(
            f"/api/content-profiles/{published['id']}/draft", json={}
        )
        draft = fork.json()["draft"]

        new_manifest = dict(draft["manifest"])
        new_manifest["track"]["entry_types"].append(
            {"key": "note", "name": "Note", "fields": []}
        )
        upd = await authenticated_client.put(
            f"/api/content-profiles/{draft['id']}",
            json={"manifest": new_manifest},
        )
        assert upd.status_code == 200, upd.text

        diff = await authenticated_client.post(
            f"/api/content-profiles/{draft['id']}/diff",
            json={"include_entry_impact": False},
        )
        assert diff.status_code == 200, diff.text
        delta = diff.json()["diff"]
        added_keys = [e["key"] for e in delta["entry_types"]["added"]]
        assert "note" in added_keys

    async def test_discard_draft_removes_it(
        self, authenticated_client: AsyncClient, test_user
    ):
        published = await self._create_published_library_cp(authenticated_client)
        fork = await authenticated_client.post(
            f"/api/content-profiles/{published['id']}/draft", json={}
        )
        draft_id = fork.json()["draft"]["id"]
        discard = await authenticated_client.post(
            f"/api/content-profiles/{draft_id}/discard-draft", json={}
        )
        assert discard.status_code == 200, discard.text
        assert discard.json()["discarded"] is True

        # Subsequent publish should now 404 / fail because the draft is gone.
        publish = await authenticated_client.post(
            f"/api/content-profiles/{draft_id}/publish", json={}
        )
        assert publish.status_code in (400, 404)

    async def test_substrate_endpoint_returns_registries(
        self, authenticated_client: AsyncClient, test_user
    ):
        resp = await authenticated_client.get("/api/content-profile-substrate")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        field_keys = {f["type"] for f in body["field_types"]}
        view_keys = {v["type"] for v in body["view_types"]}
        assert {"text", "number", "select", "relation"}.issubset(field_keys)
        assert {
            "feed",
            "kanban",
            "table",
            "composable_board",
            "composable_list",
        }.issubset(view_keys)
        assert "registry_versions" in body
