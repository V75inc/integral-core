"""Plan 07-02 — Coverage for POST /api/operational-models/from-space/{app_id}.

Locks I-LIB-03: derived library package's
``manifest['package']['provenance']`` stamps
``{source: 'space', source_id: <app_id>, derived_at: <ISO>,
   derived_by: <user_id>}``.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from httpx import AsyncClient
from PIL import Image
from pypdf import PdfWriter


def _space_manifest() -> dict:
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {"name": "from-space-libpkg"},
        "app": {
            "tracks": [
                {
                    "key": "projects",
                    "name": "Projects",
                    "provision_on_create": False,
                    "entry_types": [
                        {"key": "project", "name": "Project", "fields": []}
                    ],
                    "views": [{"key": "all", "name": "All", "view_type": "feed"}],
                    "taxonomy": {"tag_groups": []},
                }
            ],
            "relations": [],
        },
    }


async def _seed_space_with_library(authenticated_client: AsyncClient) -> str:
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Derive-space Ws"}
    )
    assert ws.status_code == 200, ws.text
    ws_id = ws.json()["workspace"]["id"]
    authenticated_client.headers["X-Integral-Scope"] = f"ws:{ws_id}"
    pub = await authenticated_client.post(
        "/api/operational-models",
        json={
            "name": "Derive-space Pack",
            "workspace_id": ws_id,
            "manifest": _space_manifest(),
        },
    )
    assert pub.status_code == 200, pub.text
    lib_id = pub.json()["operational_model"]["id"]
    sp = await authenticated_client.post(
        "/api/apps",
        json={"name": "Source App", "library_operational_model_id": lib_id},
    )
    assert sp.status_code == 200, sp.text
    app_id = sp.json()["app"]["id"]
    merge = await authenticated_client.post(
        f"/api/apps/{app_id}/operational-model/merge-library",
        json={"library_operational_model_id": lib_id},
    )
    assert merge.status_code in (200, 400), merge.text
    return app_id


@pytest.mark.asyncio
class TestDeriveLibraryFromSpace:
    """LIB-02 + ROADMAP AC#2: POST /api/operational-models/from-space/{app_id}."""

    async def test_from_space_stamps_provenance(
        self, authenticated_client: AsyncClient, test_user
    ):
        """I-LIB-03: derived manifest carries package.provenance block."""
        app_id = await _seed_space_with_library(authenticated_client)

        resp = await authenticated_client.post(
            f"/api/operational-models/from-app/{app_id}",
            json={"name": "Derived App Pack"},
        )
        assert resp.status_code == 200, resp.text
        derived = resp.json()["operational_model"]
        manifest = derived["manifest"]
        prov = manifest.get("package", {}).get("provenance")
        assert prov is not None, "provenance block missing from derived manifest"
        assert prov["source"] == "app"
        assert prov["source_id"] == app_id
        assert prov.get("derived_at"), "derived_at missing"
        assert prov.get("derived_by"), "derived_by missing"

    async def test_from_app_snapshots_live_tracks_and_entries(
        self, authenticated_client: AsyncClient, test_user
    ):
        app_id = await _seed_space_with_library(authenticated_client)
        app = (await authenticated_client.get(f"/api/apps/{app_id}")).json()["app"]
        ws_id = app["workspace_id"]
        headers = {"X-Integral-Scope": f"ws:{ws_id}"}
        track_resp = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Live project track", "app_id": app_id},
            headers=headers,
        )
        assert track_resp.status_code == 200, track_resp.text
        track_id = track_resp.json()["track"]["id"]
        entry_resp = await authenticated_client.post(
            "/api/entries",
            json={
                "track_id": track_id,
                "title": "Preserved entry",
                "body": "Snapshot body",
            },
            headers=headers,
        )
        assert entry_resp.status_code == 200, entry_resp.text
        source_entry_id = entry_resp.json()["entry"]["id"]
        pdf_buffer = BytesIO()
        pdf_writer = PdfWriter()
        pdf_writer.add_blank_page(width=72, height=72)
        pdf_writer.write(pdf_buffer)
        png_buffer = BytesIO()
        Image.new("RGB", (1, 1), color=(17, 34, 51)).save(png_buffer, format="PNG")
        file_fixtures = [
            ("source.txt", b"portable attachment", "text/plain"),
            ("source.pdf", pdf_buffer.getvalue(), "application/pdf"),
            ("source.png", png_buffer.getvalue(), "image/png"),
        ]
        for filename, content, mime_type in file_fixtures:
            attachment_resp = await authenticated_client.post(
                f"/api/entries/{source_entry_id}/attachments",
                files={"file": (filename, content, mime_type)},
                headers=headers,
            )
            assert attachment_resp.status_code == 200, attachment_resp.text

        url_resp = await authenticated_client.post(
            f"/api/entries/{source_entry_id}/attachments/url",
            json={
                "url": "https://example.com/template-reference",
                "label": "Source reference",
            },
            headers=headers,
        )
        assert url_resp.status_code == 200, url_resp.text

        resp = await authenticated_client.post(
            f"/api/operational-models/from-app/{app_id}",
            json={"name": "Structure snapshot"},
        )
        assert resp.status_code == 200, resp.text
        app_manifest = resp.json()["operational_model"]["manifest"]["app"]
        track_key = next(
            track["key"]
            for track in app_manifest["tracks"]
            if track["name"] == "Live project track"
        )
        seeds = next(
            group for group in app_manifest["seeds"] if group["track"] == track_key
        )
        assert seeds["entries"][0]["title"] == "Preserved entry"
        assert seeds["entries"][0]["body"] == "Snapshot body"
        snap_attachments = {
            item["filename"]: item for item in seeds["entries"][0]["attachments"]
        }
        assert set(snap_attachments) == {
            "source.txt",
            "source.pdf",
            "source.png",
            "Source reference",
        }
        assert snap_attachments["Source reference"]["source_type"] == "url"
        assert (
            snap_attachments["Source reference"]["external_url"]
            == "https://example.com/template-reference"
        )

        # Exercise the same installer that Manage Apps uses, then verify the
        # snapshot is materialized into a fresh App rather than only stored.
        install_resp = await authenticated_client.post(
            "/api/apps/batch-install",
            json={"items": [{"library_cp_id": resp.json()["operational_model"]["id"]}]},
            headers=headers,
        )
        assert install_resp.status_code == 200, install_resp.text
        installed_row = install_resp.json()["installed"][0]
        assert installed_row["status"] == "active", installed_row
        installed_tracks_resp = await authenticated_client.get(
            f"/api/tracks?app_id={installed_row['app_id']}", headers=headers
        )
        assert installed_tracks_resp.status_code == 200, installed_tracks_resp.text
        installed_track = next(
            track
            for track in installed_tracks_resp.json()["tracks"]
            if track["title"] == "Live project track"
        )
        entries_resp = await authenticated_client.get(
            f"/api/entries?track_id={installed_track['id']}", headers=headers
        )
        assert entries_resp.status_code == 200, entries_resp.text
        assert any(
            entry["title"] == "Preserved entry" and entry["body"] == "Snapshot body"
            for entry in entries_resp.json()["entries"]
        )
        installed_entry = next(
            entry
            for entry in entries_resp.json()["entries"]
            if entry["title"] == "Preserved entry"
        )
        attachments_resp = await authenticated_client.get(
            f"/api/entries/{installed_entry['id']}/attachments", headers=headers
        )
        assert attachments_resp.status_code == 200, attachments_resp.text
        installed_attachments = {
            item["filename"]: item for item in attachments_resp.json()["attachments"]
        }
        assert set(installed_attachments) == set(snap_attachments)
        for filename, expected_content, _mime_type in file_fixtures:
            download_resp = await authenticated_client.get(
                f"/api/attachments/{installed_attachments[filename]['id']}/download",
                headers=headers,
            )
            assert download_resp.status_code == 200, download_resp.text
            assert download_resp.content == expected_content
        assert installed_attachments["Source reference"]["source_type"] == "url"
        assert (
            installed_attachments["Source reference"]["external_url"]
            == "https://example.com/template-reference"
        )

    async def test_from_space_403_when_caller_lacks_space_update(
        self,
        authenticated_client: AsyncClient,
        second_user_client: AsyncClient,
        test_user,
    ):
        app_id = await _seed_space_with_library(authenticated_client)
        resp = await second_user_client.post(
            f"/api/operational-models/from-app/{app_id}",
            json={"name": "Foreign App Derived"},
        )
        assert resp.status_code in (403, 404), resp.text

    async def test_from_space_404_when_space_id_unknown(
        self, authenticated_client: AsyncClient, test_user
    ):
        resp = await authenticated_client.post(
            "/api/operational-models/from-app/cp-space-doesnotexist",
            json={"name": "Phantom App"},
        )
        assert resp.status_code in (403, 404), resp.text
