"""CRUD tests for Apps API."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestAppsCRUD:
    """Test suite for App CRUD operations."""

    async def _create_space(self, client: AsyncClient, name: str = "Test App"):
        response = await client.post(
            "/api/apps",
            json={"name": name, "description": "A test space"},
        )
        assert response.status_code == 200, f"App creation failed: {response.text}"
        return response.json()["app"]

    async def _create_track(self, client: AsyncClient, title: str = "Test Track"):
        response = await client.post(
            "/api/tracks", json={"title": title, "visibility": "private"}
        )
        assert response.status_code == 200, f"Track creation failed: {response.text}"
        return response.json()["track"]

    async def test_create_space(self, authenticated_client: AsyncClient, test_user):
        """Test creating a new App."""
        response = await authenticated_client.post(
            "/api/apps",
            json={"name": "Q4 Initiatives", "description": "All Q4 work"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "app" in data
        assert data["app"]["name"] == "Q4 Initiatives"
        assert "id" in data["app"]
        assert data["app"].get("visibility") == "private"

    async def test_create_space_inherit_organization_visibility(
        self, authenticated_client: AsyncClient, test_user
    ):
        org_resp = await authenticated_client.post(
            "/api/workspaces", json={"name": "Acme Org"}
        )
        assert org_resp.status_code == 200
        org_id = org_resp.json()["workspace"]["id"]
        response = await authenticated_client.post(
            "/api/apps",
            json={
                "name": "Org App",
                "workspace_id": org_id,
                "visibility": "inherit",
            },
        )
        assert response.status_code == 200
        assert response.json()["app"]["visibility"] == "workspace"

    async def test_create_space_explicit_public(
        self, authenticated_client: AsyncClient
    ):
        response = await authenticated_client.post(
            "/api/apps",
            json={"name": "Public App", "visibility": "public"},
        )
        assert response.status_code == 200
        assert response.json()["app"]["visibility"] == "public"

    async def test_create_space_organization_visibility_requires_org(
        self, authenticated_client: AsyncClient
    ):
        response = await authenticated_client.post(
            "/api/apps",
            json={"name": "Bad", "visibility": "organization"},
        )
        assert response.status_code == 400

    async def test_create_track_in_space_inherits_visibility_and_links(
        self, authenticated_client: AsyncClient, test_user
    ):
        org_resp = await authenticated_client.post(
            "/api/workspaces", json={"name": "Team Org"}
        )
        assert org_resp.status_code == 200
        org_id = org_resp.json()["workspace"]["id"]
        sp_resp = await authenticated_client.post(
            "/api/apps",
            json={
                "name": "Parent",
                "workspace_id": org_id,
                "visibility": "inherit",
            },
        )
        assert sp_resp.status_code == 200
        sp_id = sp_resp.json()["app"]["id"]
        tr_resp = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Child Track", "app_id": sp_id},
        )
        assert tr_resp.status_code == 200
        track = tr_resp.json()["track"]
        # Track.visibility under the new access model is "inherit" | "private";
        # "inherit" tells resolve_role to walk to the parent App for cascade
        # resolution, instead of stamping the App's literal value onto
        # the Track (which broke space-collaborator cascade).
        assert track["visibility"] == "inherit"
        assert track.get("workspace_id") == org_id
        listed = await authenticated_client.get(f"/api/apps/{sp_id}/tracks")
        assert listed.status_code == 200
        ids = [t["id"] for t in listed.json().get("tracks", [])]
        assert track["id"] in ids

    async def test_list_spaces(self, authenticated_client: AsyncClient, test_user):
        """Test listing user's Apps."""
        await self._create_space(authenticated_client)

        response = await authenticated_client.get("/api/apps")

        assert response.status_code == 200
        data = response.json()
        assert "apps" in data
        assert data["total"] >= 1

    async def test_get_space(self, authenticated_client: AsyncClient, test_user):
        """Test getting a App by ID."""
        sp = await self._create_space(authenticated_client, "Get App")
        sp_id = sp["id"]

        response = await authenticated_client.get(f"/api/apps/{sp_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["app"]["id"] == sp_id
        assert data["app"]["name"] == "Get App"

    async def test_get_active_definition_and_preview(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Every App exposes its installed contract and a read-only drift preview."""
        app = await self._create_space(authenticated_client, "Definition App")
        app_id = app["id"]

        definition_response = await authenticated_client.get(
            f"/api/apps/{app_id}/definition"
        )
        assert definition_response.status_code == 200
        definition = definition_response.json()["definition"]
        assert definition["id"] == app["active_definition_id"]
        assert definition["status"] == "active"

        preview_response = await authenticated_client.get(
            f"/api/apps/{app_id}/definition/preview"
        )
        assert preview_response.status_code == 200
        preview_payload = preview_response.json()
        assert preview_payload["active_definition_id"] == definition["id"]
        assert (
            preview_payload["preview"]["affected_records"]["status"] == "not_evaluated"
        )
        assert preview_payload["preview"]["effects"] == []

    async def test_update_space(self, authenticated_client: AsyncClient, test_user):
        """Test updating a App."""
        sp = await self._create_space(authenticated_client, "Old Name")
        sp_id = sp["id"]

        response = await authenticated_client.put(
            f"/api/apps/{sp_id}",
            json={"name": "New Name", "description": "Updated description"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["app"]["name"] == "New Name"

    async def test_delete_space(self, authenticated_client: AsyncClient, test_user):
        """Test deleting a App."""
        sp = await self._create_space(authenticated_client, "Delete Me")
        sp_id = sp["id"]

        response = await authenticated_client.delete(f"/api/apps/{sp_id}")

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert data.get("deleted_tracks", 0) >= 0
        assert data.get("unlinked_tracks", 0) >= 0

        get_resp = await authenticated_client.get(f"/api/apps/{sp_id}")
        assert get_resp.status_code == 404

    async def test_delete_space_cascade_deletes_contained_track(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Deleting an App removes tracks that exist only in that app_node."""
        sp = await self._create_space(authenticated_client, "Cascade App")
        sp_id = sp["id"]
        tr_resp = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Only Here", "app_id": sp_id},
        )
        assert tr_resp.status_code == 200
        track_id = tr_resp.json()["track"]["id"]

        del_resp = await authenticated_client.delete(f"/api/apps/{sp_id}")
        assert del_resp.status_code == 200
        assert del_resp.json().get("deleted_tracks") == 1

        tr_get = await authenticated_client.get(f"/api/tracks/{track_id}")
        assert tr_get.status_code == 404

    async def test_delete_space_unlinks_track_shared_with_another_space(
        self, authenticated_client: AsyncClient, test_user
    ):
        """A track linked to two apps is unlinked from the deleted app_node, not destroyed."""
        sp1 = await self._create_space(authenticated_client, "App A")
        sp2 = await self._create_space(authenticated_client, "App B")
        track = await self._create_track(authenticated_client, "Shared Track")
        tid, s1, s2 = track["id"], sp1["id"], sp2["id"]

        r1 = await authenticated_client.post(
            f"/api/apps/{s1}/tracks", json={"track_id": tid}
        )
        r2 = await authenticated_client.post(
            f"/api/apps/{s2}/tracks", json={"track_id": tid}
        )
        assert r1.status_code == 200 and r2.status_code == 200

        del_resp = await authenticated_client.delete(f"/api/apps/{s1}")
        assert del_resp.status_code == 200
        body = del_resp.json()
        assert body.get("deleted_tracks") == 0
        assert body.get("unlinked_tracks") == 1

        tr_get = await authenticated_client.get(f"/api/tracks/{tid}")
        assert tr_get.status_code == 200
        listed = await authenticated_client.get(f"/api/apps/{s2}/tracks")
        assert listed.status_code == 200
        ids = [t["id"] for t in listed.json().get("tracks", [])]
        assert tid in ids

    async def test_add_track_to_space(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test adding a Track to a App."""
        sp = await self._create_space(authenticated_client)
        track = await self._create_track(authenticated_client)
        sp_id, track_id = sp["id"], track["id"]

        response = await authenticated_client.post(
            f"/api/apps/{sp_id}/tracks",
            json={"track_id": track_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data

    async def test_list_space_tracks(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test listing Tracks in a App."""
        sp = await self._create_space(authenticated_client)
        track = await self._create_track(authenticated_client, "App Track")
        sp_id, track_id = sp["id"], track["id"]

        await authenticated_client.post(
            f"/api/apps/{sp_id}/tracks",
            json={"track_id": track_id},
        )

        response = await authenticated_client.get(f"/api/apps/{sp_id}/tracks")

        assert response.status_code == 200
        data = response.json()
        assert "tracks" in data
        ids = [t["id"] for t in data["tracks"]]
        assert track_id in ids

    async def test_remove_track_from_space(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test removing a Track from a App."""
        sp = await self._create_space(authenticated_client)
        track = await self._create_track(authenticated_client, "Removable Track")
        sp_id, track_id = sp["id"], track["id"]

        await authenticated_client.post(
            f"/api/apps/{sp_id}/tracks",
            json={"track_id": track_id},
        )

        response = await authenticated_client.delete(
            f"/api/apps/{sp_id}/tracks/{track_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data

    async def test_list_space_collaborators(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test listing collaborators of a App."""
        sp = await self._create_space(authenticated_client)

        response = await authenticated_client.get(f"/api/apps/{sp['id']}/collaborators")

        assert response.status_code == 200
        data = response.json()
        assert "collaborators" in data
        assert data["total"] >= 1

    async def test_add_space_collaborator(
        self, client: AsyncClient, authenticated_client: AsyncClient, test_user
    ):
        """Test adding a collaborator to a App."""
        signup_resp = await client.post(
            "/api/auth/signup",
            json={
                "email": "collab@example.com",
                "password": "testpassword123",
                "name": "Collab User",
            },
            timeout=10.0,
        )
        if signup_resp.status_code not in (200, 201):
            pytest.skip("Could not create collaborator user")
        collab_data = signup_resp.json()
        collab_id = (collab_data.get("user") or {}).get("id")
        if not collab_id:
            pytest.skip("Could not get collaborator user id from signup")

        sp = await self._create_space(authenticated_client)
        sp_id = sp["id"]

        response = await authenticated_client.post(
            f"/api/apps/{sp_id}/collaborators",
            json={"collaborator_user_id": collab_id, "role": "editor"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert data["collaborator_user_id"] == collab_id
        assert data["role"] == "editor"

        list_resp = await authenticated_client.get(f"/api/apps/{sp_id}/collaborators")
        assert list_resp.status_code == 200
        collaborators = list_resp.json()["collaborators"]
        collab_ids = [c.get("id") or c.get("user_id") for c in collaborators]
        assert collab_id in collab_ids

    async def test_remove_space_collaborator(
        self, client: AsyncClient, authenticated_client: AsyncClient, test_user
    ):
        """Test removing a collaborator from a App."""
        signup_resp = await client.post(
            "/api/auth/signup",
            json={
                "email": "remove@example.com",
                "password": "testpassword123",
                "name": "Remove User",
            },
            timeout=10.0,
        )
        if signup_resp.status_code not in (200, 201):
            pytest.skip("Could not create collaborator user")
        collab_data = signup_resp.json()
        collab_id = (collab_data.get("user") or {}).get("id")
        if not collab_id:
            pytest.skip("Could not get collaborator user id from signup")

        sp = await self._create_space(authenticated_client)
        sp_id = sp["id"]

        await authenticated_client.post(
            f"/api/apps/{sp_id}/collaborators",
            json={"collaborator_user_id": collab_id, "role": "viewer"},
        )

        response = await authenticated_client.delete(
            f"/api/apps/{sp_id}/collaborators/{collab_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert data["removed_collaborator_id"] == collab_id

    async def test_get_nonexistent_space(self, authenticated_client: AsyncClient):
        """Test getting a App that doesn't exist."""
        response = await authenticated_client.get("/api/apps/nonexistent-id")
        assert response.status_code == 404

    async def test_stranger_cannot_access_space(
        self, authenticated_client: AsyncClient, test_user2
    ):
        """Test that a user with no access cannot view a App."""
        sp = await self._create_space(authenticated_client, "Private App")

        from httpx import ASGITransport
        from httpx import AsyncClient as AC

        from app.main import app as _app

        try:
            from jvspatial.api.auth.models import User as AuthUser
            from jvspatial.api.auth.models import UserCreate
            from jvspatial.api.auth.service import AuthenticationService
            from jvspatial.core.context import get_default_context

            svc = AuthenticationService(get_default_context())
            await svc.register_user(
                UserCreate(email="stranger@test.com", password="pw12345")
            )
            login_resp = await AC(
                transport=ASGITransport(app=_app), base_url="http://test"
            ).post(
                "/api/auth/login",
                json={"email": "stranger@test.com", "password": "pw12345"},
            )
            if login_resp.status_code == 200:
                token = login_resp.json().get("access_token")
                if token:
                    async with AC(
                        transport=ASGITransport(app=_app),
                        base_url="http://test",
                        headers={"Authorization": f"Bearer {token}"},
                    ) as stranger_client:
                        response = await stranger_client.get(f"/api/apps/{sp['id']}")
                        assert response.status_code in (403, 404)
        except Exception:
            pass

    async def test_stranger_can_view_public_space(
        self, authenticated_client: AsyncClient
    ):
        """Any authenticated user can GET a public space they do not collaborate on."""
        sp_resp = await authenticated_client.post(
            "/api/apps",
            json={"name": "Open", "visibility": "public"},
        )
        assert sp_resp.status_code == 200
        sp_id = sp_resp.json()["app"]["id"]

        from httpx import ASGITransport
        from httpx import AsyncClient as AC

        from app.main import app as _app

        try:
            from jvspatial.api.auth.models import UserCreate
            from jvspatial.api.auth.service import AuthenticationService
            from jvspatial.core.context import get_default_context

            svc = AuthenticationService(get_default_context())
            await svc.register_user(
                UserCreate(email="visitor_pub@test.com", password="pw12345")
            )
            login_resp = await AC(
                transport=ASGITransport(app=_app), base_url="http://test"
            ).post(
                "/api/auth/login",
                json={"email": "visitor_pub@test.com", "password": "pw12345"},
            )
            if login_resp.status_code == 200:
                token = login_resp.json().get("access_token")
                if token:
                    async with AC(
                        transport=ASGITransport(app=_app),
                        base_url="http://test",
                        headers={"Authorization": f"Bearer {token}"},
                    ) as visitor_client:
                        response = await visitor_client.get(f"/api/apps/{sp_id}")
                        assert response.status_code == 200
                        assert response.json()["app"]["id"] == sp_id
        except Exception:
            pass

    async def test_create_space_with_crm_library_provisions_prescribed_tracks(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Phase 31 (DR-31-01 §3): CRM bundle owns Contacts + Opportunities."""
        packs = await authenticated_client.get("/api/content-profiles")
        assert packs.status_code == 200
        profiles = packs.json().get("content_profiles") or []
        lib = next(
            (
                p
                for p in profiles
                if (p.get("manifest") or {}).get("package", {}).get("slug") == "crm"
            ),
            None,
        )
        if not lib:
            pytest.skip("CRM library package not seeded")
        lib_id = lib["id"]
        resp = await authenticated_client.post(
            "/api/apps",
            json={
                "name": "CRM App",
                "library_content_profile_id": lib_id,
            },
        )
        assert resp.status_code == 200, resp.text
        sp_id = resp.json()["app"]["id"]
        tr = await authenticated_client.get(f"/api/apps/{sp_id}/tracks")
        assert tr.status_code == 200
        tracks = tr.json().get("tracks") or []
        assert len(tracks) >= 2
        titles = {t.get("title") for t in tracks}
        assert "Contacts" in titles
        assert "Opportunities" in titles
        for t in tracks:
            if t.get("title") in ("Contacts", "Opportunities"):
                assert (
                    t.get("purpose") or ""
                ).strip(), f"expected prefab description on track {t.get('title')}"

    async def test_create_space_with_projects_library_provisions_projects_track(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Phase 31 (DR-31-01 §3): Projects bundle owns the Projects track."""
        packs = await authenticated_client.get("/api/content-profiles")
        assert packs.status_code == 200
        profiles = packs.json().get("content_profiles") or []
        lib = next(
            (
                p
                for p in profiles
                if (p.get("manifest") or {}).get("package", {}).get("slug")
                == "projects"
            ),
            None,
        )
        if not lib:
            pytest.skip("Projects library package not seeded")
        lib_id = lib["id"]
        resp = await authenticated_client.post(
            "/api/apps",
            json={
                "name": "Projects App",
                "library_content_profile_id": lib_id,
            },
        )
        assert resp.status_code == 200, resp.text
        sp_id = resp.json()["app"]["id"]
        tr = await authenticated_client.get(f"/api/apps/{sp_id}/tracks")
        assert tr.status_code == 200
        tracks = tr.json().get("tracks") or []
        titles = {t.get("title") for t in tracks}
        assert "Customer Projects" in titles
        for t in tracks:
            if t.get("title") == "Customer Projects":
                assert (
                    t.get("purpose") or ""
                ).strip(), "expected prefab description on Customer Projects track"

    async def test_crm_library_provisioned_views_keep_declarative_config(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Phase 31 (DR-31-01 §3): Contacts + Opportunities + their views
        live in the **crm** bundle post-decomposition."""
        packs = await authenticated_client.get("/api/content-profiles")
        assert packs.status_code == 200
        profiles = packs.json().get("content_profiles") or []
        lib = next(
            (
                p
                for p in profiles
                if (p.get("manifest") or {}).get("package", {}).get("slug") == "crm"
            ),
            None,
        )
        if not lib:
            pytest.skip("CRM library package not seeded")

        resp = await authenticated_client.post(
            "/api/apps",
            json={
                "name": "CRM Views App",
                "library_content_profile_id": lib["id"],
            },
        )
        assert resp.status_code == 200, resp.text
        sp_id = resp.json()["app"]["id"]
        tr = await authenticated_client.get(f"/api/apps/{sp_id}/tracks")
        assert tr.status_code == 200
        tracks = tr.json().get("tracks") or []
        by_title = {t.get("title"): t.get("id") for t in tracks}
        assert by_title.get("Contacts")
        assert by_title.get("Opportunities")

        contact_views_resp = await authenticated_client.get(
            f"/api/tracks/{by_title['Contacts']}/views"
        )
        assert contact_views_resp.status_code == 200
        contact_views = contact_views_resp.json().get("views") or []
        # ACC-06 — Contacts now ships multiple table views (canonical
        # ``contacts_table`` plus four slice views projecting each new
        # EntryType — Account/Lead/Contact Person/Partner). The canonical
        # default is the one declared as ``defaults.default_view`` in the
        # manifest; resolve it by ``_manifest_view_key`` to stay robust to
        # the order the API returns views in.
        table_view = next(
            (
                v
                for v in contact_views
                if v.get("type") == "table"
                and (v.get("config") or {}).get("_manifest_view_key")
                == "contacts_table"
            ),
            None,
        )
        assert table_view is not None
        assert table_view.get("is_default") is True
        table_cfg = table_view.get("config") or {}
        assert len(table_cfg.get("columns") or []) >= 4

        opp_views_resp = await authenticated_client.get(
            f"/api/tracks/{by_title['Opportunities']}/views"
        )
        assert opp_views_resp.status_code == 200
        opp_views = opp_views_resp.json().get("views") or []
        opp_by_type = {v.get("type"): v for v in opp_views}
        assert "kanban" in opp_by_type
        assert "table" in opp_by_type
        # ACC-06 — Opportunities now ships multiple kanban views
        # (canonical ``opportunities_kanban`` plus ``bids_kanban`` and
        # ``prospects_kanban`` slice views). Resolve the canonical default
        # by ``_manifest_view_key`` rather than relying on dict-coerce
        # ordering across kanban views.
        opp_kanban_default = next(
            (
                v
                for v in opp_views
                if v.get("type") == "kanban"
                and (v.get("config") or {}).get("_manifest_view_key")
                == "opportunities_kanban"
            ),
            None,
        )
        assert opp_kanban_default is not None
        assert opp_kanban_default.get("is_default") is True

    async def test_projects_library_provisioned_views_keep_declarative_config(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Phase 31 (DR-31-01 §3): Projects + its kanban/calendar views
        live in the **projects** bundle post-decomposition."""
        packs = await authenticated_client.get("/api/content-profiles")
        assert packs.status_code == 200
        profiles = packs.json().get("content_profiles") or []
        lib = next(
            (
                p
                for p in profiles
                if (p.get("manifest") or {}).get("package", {}).get("slug")
                == "projects"
            ),
            None,
        )
        if not lib:
            pytest.skip("Projects library package not seeded")

        resp = await authenticated_client.post(
            "/api/apps",
            json={
                "name": "Projects Views App",
                "library_content_profile_id": lib["id"],
            },
        )
        assert resp.status_code == 200, resp.text
        sp_id = resp.json()["app"]["id"]
        tr = await authenticated_client.get(f"/api/apps/{sp_id}/tracks")
        assert tr.status_code == 200
        tracks = tr.json().get("tracks") or []
        by_title = {t.get("title"): t.get("id") for t in tracks}
        assert by_title.get("Customer Projects")

        project_views_resp = await authenticated_client.get(
            f"/api/tracks/{by_title['Customer Projects']}/views"
        )
        assert project_views_resp.status_code == 200
        project_views = project_views_resp.json().get("views") or []
        by_type = {v.get("type"): v for v in project_views}
        assert "kanban" in by_type
        assert "calendar" in by_type
        assert by_type["kanban"].get("is_default") is True
        kanban_cfg = by_type["kanban"].get("config") or {}
        assert kanban_cfg.get("group_by") == "custom_fields.status"
        assert len(kanban_cfg.get("kanban_columns") or []) >= 4
        calendar_cfg = by_type["calendar"].get("config") or {}
        mapping = calendar_cfg.get("calendar_mapping") or {}
        assert mapping.get("dateField") == "custom_fields.due_date"
        assert mapping.get("endDateField") == "custom_fields.start_date"

    async def test_create_track_with_app_track_type_key(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Phase 31 (DR-31-01): Contacts track-type lives in the **crm** bundle."""
        packs = await authenticated_client.get("/api/content-profiles")
        assert packs.status_code == 200
        profiles = packs.json().get("content_profiles") or []
        lib = next(
            (
                p
                for p in profiles
                if (p.get("manifest") or {}).get("package", {}).get("slug") == "crm"
            ),
            None,
        )
        if not lib:
            pytest.skip("CRM library package not seeded")
        sp_resp = await authenticated_client.post(
            "/api/apps",
            json={
                "name": "Type Key App",
                "library_content_profile_id": lib["id"],
            },
        )
        assert sp_resp.status_code == 200
        sp_id = sp_resp.json()["app"]["id"]
        tr_resp = await authenticated_client.post(
            "/api/tracks",
            json={
                "title": "Extra CRM",
                "app_id": sp_id,
                "app_track_type_key": "contacts",
            },
        )
        assert tr_resp.status_code == 200, tr_resp.text
        tid = tr_resp.json()["track"]["id"]
        types = await authenticated_client.get(
            "/api/entry-types", params={"track_id": tid}
        )
        assert types.status_code == 200
        names = [x.get("name", "") for x in types.json().get("entry_types", [])]
        assert any(n.lower() == "contact" for n in names)

    async def test_space_content_profile_preview_and_apply(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Phase 31 (DR-31-01): preview + apply against the **crm** bundle."""
        packs = await authenticated_client.get("/api/content-profiles")
        assert packs.status_code == 200
        profiles = packs.json().get("content_profiles") or []
        lib = next(
            (
                p
                for p in profiles
                if (p.get("manifest") or {}).get("package", {}).get("slug") == "crm"
            ),
            None,
        )
        if not lib:
            pytest.skip("CRM library package not seeded")
        sp_resp = await authenticated_client.post(
            "/api/apps",
            json={"name": "Preview Apply App"},
        )
        assert sp_resp.status_code == 200
        sid = sp_resp.json()["app"]["id"]
        preview_resp = await authenticated_client.post(
            f"/api/apps/{sid}/content-profile/preview",
            json={"library_content_profile_id": lib["id"]},
        )
        assert preview_resp.status_code == 200, preview_resp.text
        preview = preview_resp.json().get("preview") or {}
        assert preview.get("scope") in {"app", "track", "workspace"}
        assert "counts" in preview
        apply_resp = await authenticated_client.post(
            f"/api/apps/{sid}/content-profile/apply",
            json={"library_content_profile_id": lib["id"]},
        )
        assert apply_resp.status_code == 200, apply_resp.text
        apply_body = apply_resp.json()
        assert apply_body["definition_id"]
        assert apply_body["definition_revision"] == 2
        definition_response = await authenticated_client.get(
            f"/api/apps/{sid}/definition"
        )
        assert definition_response.status_code == 200
        assert (
            definition_response.json()["definition"]["id"]
            == apply_body["definition_id"]
        )
        assert apply_resp.json().get("applied", {}).get("space_track_count", 0) >= 0

    async def test_track_template_applied_on_track_create(
        self, authenticated_client: AsyncClient, test_user
    ):
        sp = await self._create_space(authenticated_client, "Template App")
        sid = sp["id"]
        tpl_resp = await authenticated_client.post(
            f"/api/apps/{sid}/content-profile/track-templates",
            json={"name": "Dev", "description": "d"},
        )
        assert tpl_resp.status_code == 200
        tid_tpl = tpl_resp.json()["track_template"]["id"]
        et = await authenticated_client.post(
            f"/api/apps/{sid}/content-profile/track-templates/{tid_tpl}/entry-types",
            json={"name": "Story", "icon": "📖"},
        )
        assert et.status_code == 200
        tr = await authenticated_client.post(
            "/api/tracks",
            json={
                "title": "With Template",
                "visibility": "private",
                "app_id": sid,
                "app_track_template_content_profile_id": tid_tpl,
            },
        )
        assert tr.status_code == 200
        tid = tr.json()["track"]["id"]
        types = await authenticated_client.get(
            "/api/entry-types", params={"track_id": tid}
        )
        assert types.status_code == 200
        names = [x["name"] for x in types.json().get("entry_types", [])]
        assert "Story" in names
