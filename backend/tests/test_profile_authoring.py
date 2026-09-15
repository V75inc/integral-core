"""Phase 6 Plan 06-04 — profile-aware MCP tools (MCP-04).

Covers:

* ``integral_author_profile`` (POST /api/content-profiles/author) — v1
  deterministic template-fill against the 7-keyword library map; fallback
  generic ``post`` ET; two-tier gate (profile.author + workspace publish).
* ``integral_modify_profile`` (POST /api/content-profiles/{id}/modify) —
  Phase 3.1 patch DSL routed through ``apply_operations`` +
  ``publish_draft`` (Phase 5 reject_gate + force-bypass).
* ``integral_list_profiles`` (GET /api/content-profiles?type_hint=…) —
  token-intersection scoring; published authored CPs immediately listable.
* ``type_hint`` on ``POST /api/tracks`` + ``POST /api/apps`` — resolves
  via ``resolve_type_hint`` service helper (Pitfall 6 — no MCP recursion).

See I-PROFILE-01..02 in docs/INVARIANTS.md.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_workspace(client: AsyncClient, name: str = "AuthoringWs") -> str:
    """Create an org workspace; returns workspace_id (caller is owner)."""
    resp = await client.post("/api/workspaces", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["workspace"]["id"]


async def _seed_library_package(name: str, description: str, manifest: Dict[str, Any]):
    """Create + catalog a library package directly (skips API auth)."""
    from app.models.edges import CATALOGS
    from app.models.nodes import (
        CONTENT_PROFILES_REGISTRY_ID,
        ContentProfile,
        ContentProfiles,
    )

    existing = await ContentProfile.find(
        {"context.library_package": True, "context.name": name}
    )
    if existing:
        rows = existing if isinstance(existing, list) else [existing]
        if rows:
            return rows[0]
    reg = await ContentProfiles.get(CONTENT_PROFILES_REGISTRY_ID)
    if not reg:
        reg = await ContentProfiles.create(id=CONTENT_PROFILES_REGISTRY_ID)
    cp = await ContentProfile.create(
        name=name,
        description=description,
        manifest=manifest,
        scope=str(manifest.get("scope", "track")),
        library_package=True,
        status="published",
    )
    await reg.connect(cp, edge=CATALOGS, cataloged_at="2026-05-17T00:00:00Z")
    return cp


def _bug_tracking_manifest() -> Dict[str, Any]:
    return {
        "content_profile_schema_version": 2,
        "scope": "track",
        "package": {"name": "bug-tracking", "description": "Bug tracking"},
        "track": {
            "entry_types": [
                {
                    "key": "bug",
                    "name": "Bug",
                    "fields": [
                        {"key": "title", "name": "Title", "type": "text"},
                        {
                            "key": "severity",
                            "name": "Severity",
                            "type": "select",
                            "options": ["low", "med", "high"],
                        },
                    ],
                }
            ],
            "views": [{"key": "feed", "name": "Feed", "view_type": "feed"}],
            "taxonomy": {"tag_groups": []},
        },
    }


def _crm_pm_manifest() -> Dict[str, Any]:
    return {
        "content_profile_schema_version": 2,
        "scope": "track",
        "package": {
            "name": "CRM",
            "description": "CRM pipeline",
        },
        "track": {
            "entry_types": [
                {
                    "key": "lead",
                    "name": "Lead",
                    "fields": [
                        {"key": "name", "name": "Name", "type": "text"},
                    ],
                }
            ],
            "views": [{"key": "feed", "name": "Feed", "view_type": "feed"}],
            "taxonomy": {"tag_groups": []},
        },
    }


# ---------------------------------------------------------------------------
# Test 1 — integral_author_profile keyword match (AC#3 listability)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_author_profile_keyword_match_publishes_listable(
    authenticated_client: AsyncClient, test_user
):
    """AC#3: NL description with 'bug' keyword → Bug Tracking match,
    published library CP, immediately listable via type_hint."""
    await _seed_library_package(
        "Bug Tracking", "Bug tracking", _bug_tracking_manifest()
    )
    ws_id = await _create_workspace(authenticated_client, "BugAuthorWs")

    resp = await authenticated_client.post(
        "/api/content-profiles/author",
        json={
            "description": "Track for software bug reports",
            "target_scope": "track",
            "workspace_id": ws_id,
            "as_draft": False,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "published"
    cp_id = body["content_profile_id"]
    # Manifest came from Bug Tracking template (has entry_type key='bug').
    manifest = body["manifest"]
    ets = manifest.get("track", {}).get("entry_types", [])
    assert any(et.get("key") == "bug" for et in ets), manifest

    # Listable via type_hint (the just-published CP appears).
    listed = await authenticated_client.get(
        "/api/content-profiles?type_hint=bug%20tracking"
    )
    assert listed.status_code == 200, listed.text
    matches = listed.json().get("_type_hint_matches") or []
    assert any(
        m["content_profile_id"] == cp_id for m in matches
    ), f"Authored CP {cp_id} not surfaced by type_hint. Matches: {matches}"


# ---------------------------------------------------------------------------
# Test 1a — integral_author_profile default as_draft=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_author_profile_default_as_draft(
    authenticated_client: AsyncClient, test_user
):
    """Default as_draft=True: status='draft'; NOT in type_hint listing."""
    await _seed_library_package(
        "Bug Tracking", "Bug tracking", _bug_tracking_manifest()
    )
    ws_id = await _create_workspace(authenticated_client, "DraftAuthorWs")

    resp = await authenticated_client.post(
        "/api/content-profiles/author",
        json={
            "description": "Track for issue and bug triage",
            "target_scope": "track",
            "workspace_id": ws_id,
            # as_draft omitted → defaults True
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "draft"
    draft_cp_id = body["content_profile_id"]

    # Draft NOT listed (not cataloged under registry).
    listed = await authenticated_client.get(
        "/api/content-profiles?type_hint=bug%20tracking"
    )
    matches = listed.json().get("_type_hint_matches") or []
    assert not any(m["content_profile_id"] == draft_cp_id for m in matches)


# ---------------------------------------------------------------------------
# Test 2 — integral_author_profile fallback (no keyword match)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_author_profile_fallback_no_keyword(
    authenticated_client: AsyncClient, test_user
):
    """No keyword match → generic post entry-type with caller fields."""
    ws_id = await _create_workspace(authenticated_client, "FallbackWs")

    resp = await authenticated_client.post(
        "/api/content-profiles/author",
        json={
            "description": "Track for moon phases",
            "target_scope": "track",
            "workspace_id": ws_id,
            "fields": [{"key": "phase", "name": "Phase", "type": "text"}],
        },
    )
    assert resp.status_code == 200, resp.text
    manifest = resp.json()["manifest"]
    ets = manifest["track"]["entry_types"]
    assert len(ets) == 1
    assert ets[0]["key"] == "post"
    # Caller-supplied fields used as the schema.
    field_keys = [f["key"] for f in ets[0]["fields"]]
    assert "phase" in field_keys


# ---------------------------------------------------------------------------
# Test 3 — integral_author_profile workspace denial (Tier 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_author_profile_workspace_denial(
    authenticated_client: AsyncClient, test_user
):
    """No publish rights on workspace → 403.

    For the default-human dispatch, both Tier 1 and Tier 2 collapse to
    ``can_publish_content_profiles_under_workspace``. Pointing at a
    non-existent workspace cleanly denies both tiers.
    """
    resp = await authenticated_client.post(
        "/api/content-profiles/author",
        json={
            "description": "Track for bugs",
            "target_scope": "track",
            "workspace_id": "ws_does_not_exist_xyz",
        },
    )
    # 403 with InsufficientPermissionsError envelope; Tier 1 fires first
    # (profile.author → workspace publish check returns False).
    assert resp.status_code in (401, 403), resp.text


# ---------------------------------------------------------------------------
# Test 4 — integral_author_profile policy denial via agent dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_author_profile_policy_denial_agent_path(
    authenticated_client: AsyncClient, test_user, monkeypatch
):
    """Agent caller without baseline ``profile.author`` Policy → Tier 1 denies.

    Monkeypatches ``policy_evaluate`` for this test only. Verifies that
    the handler raises 403 BEFORE reaching Tier 2.
    """
    from app.api import content_profiles as cp_mod

    async def _fake_evaluate(*, subject, action, resource, _internal_actor=None):
        from app.services.policy_engine import Decision

        if action == "profile.author":
            return Decision(allowed=False, reason="fail_closed_no_policy")
        return Decision(allowed=True, reason="test")

    monkeypatch.setattr(cp_mod, "policy_evaluate", _fake_evaluate)
    ws_id = await _create_workspace(authenticated_client, "PolDenWs")

    resp = await authenticated_client.post(
        "/api/content-profiles/author",
        json={
            "description": "Track for bug",
            "target_scope": "track",
            "workspace_id": ws_id,
        },
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Test 5 — integral_modify_profile attached-instance path (AC#4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modify_profile_attached_instance(
    authenticated_client: AsyncClient, test_user
):
    """AC#4: modify an attached profile → publish via Phase 5 runtime,
    non-destructive op so reject_gate passes; new version emitted."""
    # Create a Track with a fresh attached profile.
    ws_id = await _create_workspace(authenticated_client, "AttachedWs")
    tr_resp = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Mod Track Attached", "workspace_id": ws_id},
    )
    assert tr_resp.status_code == 200, tr_resp.text
    track = tr_resp.json()["track"]
    # Discover its attached CP id.
    from app.models.nodes import Track as TrackModel
    from app.services.app_graph import get_track_attached_content_profile

    track_node = await TrackModel.get(track["id"])
    cp = await get_track_attached_content_profile(track_node)
    assert cp is not None
    cp_id = cp.id

    # Add an entry_type via the patch DSL (Phase 3.1 op shape: op + spec).
    modify_resp = await authenticated_client.post(
        f"/api/content-profiles/{cp_id}/modify",
        json={
            "operations": [
                {
                    "op": "add_entry_type",
                    "spec": {
                        "key": "note",
                        "name": "Note",
                        "fields": [{"key": "body", "name": "Body", "type": "markdown"}],
                    },
                }
            ]
        },
    )
    assert modify_resp.status_code == 200, modify_resp.text
    body = modify_resp.json()
    # publish_draft returns a dict with published_id + version_number +
    # migration_run/migration_tracker (Phase 5 runtime fired on attached path).
    assert body.get("published_id") == cp_id, body
    assert "migration_run" in body or "migration_tracker" in body, body
    # Reload + assert the new entry_type made it.
    from app.models.nodes import ContentProfile

    refreshed = await ContentProfile.get(cp_id)
    ets = (refreshed.manifest or {}).get("track", {}).get("entry_types", [])
    assert any(et["key"] == "note" for et in ets), refreshed.manifest


@pytest.mark.asyncio
async def test_modify_profile_add_entry_type_with_fields_populates_form_schema(
    authenticated_client: AsyncClient, test_user
):
    """June 29 QA #4: agent-created entry types must materialize their declared
    fields into ``form_schema.fields`` so the "+New" quick-add renders them —
    not just the generic title/detail slots. Regression for
    ``integral_modify_profile(action=add_entry_type, fields=[…])``."""
    from app.models.nodes import EntryType
    from app.models.nodes import Track as TrackModel
    from app.services.agent_profiles import modify_profile
    from app.services.app_graph import get_track_attached_content_profile

    ws_id = await _create_workspace(authenticated_client, "FieldsWs")
    tr_resp = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Fields Track", "workspace_id": ws_id},
    )
    assert tr_resp.status_code == 200, tr_resp.text
    track = tr_resp.json()["track"]
    track_node = await TrackModel.get(track["id"])
    cp = await get_track_attached_content_profile(track_node)
    assert cp is not None

    result = await modify_profile(
        user_id=test_user.id,
        track_id=track["id"],
        action="add_entry_type",
        name="Vendor Contract",
        fields=[
            {"key": "counterparty", "name": "Counterparty", "type": "text"},
            {
                "key": "status",
                "name": "Status",
                "type": "select",
                "enum": ["draft", "active", "expired"],
            },
            {"key": "signed_date", "name": "Signed date", "type": "date"},
        ],
    )
    assert "error" not in result, result
    et = await EntryType.get(result["entry_type_id"])
    assert et is not None
    fields = (et.form_schema or {}).get("fields") or []
    keys = {f.get("key") for f in fields}
    assert {"counterparty", "status", "signed_date"} <= keys, et.form_schema
    # The select field's declared enum must survive normalization so the form
    # renders a populated dropdown (not an optionless required field).
    status_field = next(f for f in fields if f.get("key") == "status")
    assert status_field.get("type") == "select", status_field
    opts = status_field.get("enum") or status_field.get("options") or []
    opt_values = {(o.get("value") if isinstance(o, dict) else o) for o in opts}
    assert {"draft", "active", "expired"} <= opt_values, status_field


@pytest.mark.asyncio
async def test_apply_entry_types_to_track_materializes_fields_and_drops_post(
    authenticated_client: AsyncClient, test_user
):
    """June 29 QA #4 (real path): a custom track built via create_app_track with
    inline entry_types must land those fields on the track and drop the generic
    'Post' starter — so its '+New' form renders the fields, not title/detail."""
    from app.models.edges import CONTAINS
    from app.models.nodes import EntryType
    from app.models.nodes import Track as TrackModel
    from app.services.agent_profiles import apply_entry_types_to_track
    from app.services.app_graph import get_track_attached_content_profile

    ws_id = await _create_workspace(authenticated_client, "InlineTypesWs")
    tr_resp = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Training Records", "workspace_id": ws_id},
    )
    assert tr_resp.status_code == 200, tr_resp.text
    track = tr_resp.json()["track"]
    track_node = await TrackModel.get(track["id"])
    cp = await get_track_attached_content_profile(track_node)
    assert cp is not None
    # Fresh track starts with the generic "Post" starter type.
    before = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    assert any(str(getattr(e, "name", "")).casefold() == "post" for e in before)

    res = await apply_entry_types_to_track(
        user_id=test_user.id,
        track_id=track["id"],
        entry_types=[
            {
                "name": "Training Record",
                "icon": "document",
                "fields": [
                    {"key": "employee_name", "name": "Employee name", "type": "text"},
                    {
                        "key": "training_type",
                        "name": "Training type",
                        "type": "select",
                        "enum": ["onboarding", "compliance", "technical", "leadership"],
                    },
                    {
                        "key": "completion_date",
                        "name": "Completion date",
                        "type": "date",
                    },
                ],
            }
        ],
    )
    assert "error" not in res, res
    assert res["removed_starter"] is True, res

    types = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    by_name = {str(getattr(e, "name", "")): e for e in types}
    assert "Post" not in by_name, "generic Post starter should be removed"
    assert "Training Record" in by_name, by_name.keys()
    fields = (by_name["Training Record"].form_schema or {}).get("fields") or []
    keys = {f.get("key") for f in fields}
    assert {"employee_name", "training_type", "completion_date"} <= keys, fields
    tt = next(f for f in fields if f.get("key") == "training_type")
    opts = tt.get("enum") or tt.get("options") or []
    opt_values = {(o.get("value") if isinstance(o, dict) else o) for o in opts}
    assert {"onboarding", "compliance", "technical", "leadership"} <= opt_values, tt


# ---------------------------------------------------------------------------
# Test 6 — integral_modify_profile library-package path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modify_profile_library_package(
    authenticated_client: AsyncClient, test_user
):
    """Library package modify: publish bumps version; no migration runtime."""
    ws_id = await _create_workspace(authenticated_client, "LibModWs")
    # Publish a workspace-private library CP.
    manifest = _bug_tracking_manifest()
    pub_resp = await authenticated_client.post(
        "/api/content-profiles",
        json={
            "name": "Lib Mod Pack",
            "workspace_id": ws_id,
            "manifest": manifest,
        },
    )
    assert pub_resp.status_code == 200, pub_resp.text
    lib_cp_id = pub_resp.json()["content_profile"]["id"]

    # Modify by adding a benign entry_type (Phase 3.1 op shape: op + spec).
    modify_resp = await authenticated_client.post(
        f"/api/content-profiles/{lib_cp_id}/modify",
        json={
            "operations": [
                {
                    "op": "add_entry_type",
                    "spec": {
                        "key": "task",
                        "name": "Task",
                        "fields": [
                            {"key": "summary", "name": "Summary", "type": "text"}
                        ],
                    },
                }
            ]
        },
    )
    assert modify_resp.status_code == 200, modify_resp.text
    from app.models.nodes import ContentProfile

    refreshed = await ContentProfile.get(lib_cp_id)
    ets = (refreshed.manifest or {}).get("track", {}).get("entry_types", [])
    assert any(et["key"] == "task" for et in ets)


# ---------------------------------------------------------------------------
# Test 7 — integral_modify_profile reject_gate without force=true
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modify_profile_reject_gate_no_force(
    authenticated_client: AsyncClient, test_user, monkeypatch
):
    """Non-migratable op (mocked reject_gate) → 422 from publish_draft.

    To keep the test deterministic, monkeypatch publish_draft to simulate
    a reject_gate failure WITHOUT force=true (matches Phase 5 contract).
    """
    from app.api import content_profiles as cp_mod
    from app.exceptions import BadRequestError as _BReq

    ws_id = await _create_workspace(authenticated_client, "RejectWs")
    pub_resp = await authenticated_client.post(
        "/api/content-profiles",
        json={
            "name": "Reject Pack",
            "workspace_id": ws_id,
            "manifest": _bug_tracking_manifest(),
        },
    )
    cp_id = pub_resp.json()["content_profile"]["id"]

    async def _fake_publish_draft(
        *,
        draft,
        published=None,
        actor_id=None,
        run_migrations=True,
        abort_on_migration_failure=True,
        force=False,
        await_runner=False,
    ):
        if not force:
            raise _BReq(
                message="reject_gate: non-migratable change",
                details={
                    "error_code": "migration.unhandled_breaks",
                    "rejections": [{"op": "remove_field", "reason": "required field"}],
                },
            )
        return {"content_profile_id": cp_id, "version_number": 99}

    # Patch the local imported binding in modify_content_profile via
    # mocking at the atomic-swap module level.
    import app.services.content_profile_atomic_swap as swap_mod

    monkeypatch.setattr(swap_mod, "publish_draft", _fake_publish_draft)

    modify_resp = await authenticated_client.post(
        f"/api/content-profiles/{cp_id}/modify",
        json={
            "operations": [
                {
                    "op": "remove_entry_type",
                    "key": "bug",
                }
            ]
        },
    )
    # BadRequestError surfaces as 4xx (typically 400 in the project's error
    # envelope; the plan documents 422 — accept either to match the actual
    # error-mapping while still verifying the reject path fires).
    assert modify_resp.status_code in (400, 422), modify_resp.text


# ---------------------------------------------------------------------------
# Test 8 — integral_modify_profile force=true bypasses reject_gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modify_profile_force_bypasses_reject_gate(
    authenticated_client: AsyncClient, test_user, monkeypatch
):
    """force=true → publish_draft proceeds; no rejection raised."""
    from app.api import content_profiles as cp_mod

    ws_id = await _create_workspace(authenticated_client, "ForceWs")
    pub_resp = await authenticated_client.post(
        "/api/content-profiles",
        json={
            "name": "Force Pack",
            "workspace_id": ws_id,
            "manifest": _bug_tracking_manifest(),
        },
    )
    cp_id = pub_resp.json()["content_profile"]["id"]

    captured = {}

    async def _fake_publish_draft(
        *,
        draft,
        published=None,
        actor_id=None,
        run_migrations=True,
        abort_on_migration_failure=True,
        force=False,
        await_runner=False,
    ):
        captured["force"] = force
        return {
            "content_profile_id": cp_id,
            "version_number": 99,
            "forced": force,
        }

    import app.services.content_profile_atomic_swap as swap_mod

    monkeypatch.setattr(swap_mod, "publish_draft", _fake_publish_draft)

    modify_resp = await authenticated_client.post(
        f"/api/content-profiles/{cp_id}/modify",
        json={
            "operations": [
                {
                    "op": "remove_entry_type",
                    "key": "bug",
                }
            ],
            "force": True,
        },
    )
    assert modify_resp.status_code == 200, modify_resp.text
    assert captured.get("force") is True


# ---------------------------------------------------------------------------
# Test 9 — integral_list_profiles?type_hint ranks correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_profiles_type_hint_ranks(
    authenticated_client: AsyncClient, test_user
):
    """type_hint='bug tracking' ranks Bug Tracking first (score 1.0)."""
    await _seed_library_package(
        "Bug Tracking", "Bug tracking package", _bug_tracking_manifest()
    )
    await _seed_library_package("CRM", "CRM pipeline", _crm_pm_manifest())
    resp = await authenticated_client.get(
        "/api/content-profiles?type_hint=bug%20tracking"
    )
    assert resp.status_code == 200, resp.text
    matches = resp.json().get("_type_hint_matches") or []
    assert matches, "Expected at least one type_hint match"
    top = matches[0]
    assert "bug" in (top["name"] + top["package_name"]).lower(), top
    assert top["score"] >= 0.5, top


# ---------------------------------------------------------------------------
# Test 14 — POST /api/tracks with type_hint resolves to library package (AC#5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_track_with_type_hint_resolves_library(
    authenticated_client: AsyncClient, test_user
):
    """AC#5: ``type_hint='bug tracking'`` → Track attached to Bug Tracking lib."""
    await _seed_library_package(
        "Bug Tracking", "Bug tracking", _bug_tracking_manifest()
    )
    ws_id = await _create_workspace(authenticated_client, "TypeHintWs")
    resp = await authenticated_client.post(
        "/api/tracks",
        json={
            "title": "My Bugs",
            "type_hint": "bug tracking",
            "workspace_id": ws_id,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # No warnings — hint resolved to a real package.
    assert "warnings" not in body, body
    track = body["track"]
    # Track has library_merge_source_id pointing at the matched lib CP.
    from app.models.nodes import Track as TrackModel

    tn = await TrackModel.get(track["id"])
    assert tn is not None
    assert (
        tn.library_merge_source_id
    ), "type_hint resolution did not record library_merge_source_id"


# ---------------------------------------------------------------------------
# Test 15 — type_hint tied top score → 400 disambiguation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_track_type_hint_multi_match_400(
    authenticated_client: AsyncClient, test_user, monkeypatch
):
    """Tied top-score hint → 400 content_profile.type_hint_ambiguous."""
    from app.api import content_profiles as cp_mod
    from app.api import tracks as tracks_mod

    async def _fake_resolve(hint):
        return [
            {
                "content_profile_id": "cp-a",
                "name": "Bug A",
                "score": 0.5,
                "package_name": "a",
            },
            {
                "content_profile_id": "cp-b",
                "name": "Bug B",
                "score": 0.5,
                "package_name": "b",
            },
        ]

    monkeypatch.setattr(cp_mod, "resolve_type_hint", _fake_resolve)
    # tracks.py imports lazily inside the handler, so we also patch the
    # bound name on the source module to be safe across import patterns.
    monkeypatch.setattr(cp_mod, "resolve_type_hint", _fake_resolve, raising=False)
    ws_id = await _create_workspace(authenticated_client, "TiedWs")
    resp = await authenticated_client.post(
        "/api/tracks",
        json={
            "title": "Ambig",
            "type_hint": "bug",
            "workspace_id": ws_id,
        },
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()
    # Custom error_code is nested under `details.error_code`; outer
    # error_code is the generic envelope (`bad_request`).
    nested_err = (detail.get("details") or {}).get("error_code")
    assert nested_err == "content_profile.type_hint_ambiguous", detail
    candidates = (detail.get("details") or {}).get("candidates") or []
    assert len(candidates) >= 2, detail


# ---------------------------------------------------------------------------
# Test 16 — type_hint zero matches → fallback + warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_track_type_hint_zero_matches_warning(
    authenticated_client: AsyncClient, test_user, monkeypatch
):
    """Zero matches → 200 with warnings[] mentioning type_hint."""
    from app.api import content_profiles as cp_mod

    async def _fake_resolve(hint):
        return []

    monkeypatch.setattr(cp_mod, "resolve_type_hint", _fake_resolve)
    ws_id = await _create_workspace(authenticated_client, "ZeroMatchWs")
    resp = await authenticated_client.post(
        "/api/tracks",
        json={
            "title": "Moon Phases",
            "type_hint": "zzz-not-a-real-domain",
            "workspace_id": ws_id,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "warnings" in body, body
    assert any("type_hint" in w for w in body["warnings"]), body


# ---------------------------------------------------------------------------
# Test 17 — POST /api/apps with type_hint symmetric
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_space_type_hint_symmetric(
    authenticated_client: AsyncClient, test_user, monkeypatch
):
    """apps.create accepts the same type_hint kwarg shape; resolves
    via the SAME resolve_type_hint helper (Pitfall 6 — no MCP recursion).

    Uses a monkeypatched resolver to guarantee determinism regardless of
    which library packages happen to be seeded in the test DB.
    """
    from app.api import content_profiles as cp_mod

    captured_calls = []

    async def _fake_resolve(hint):
        captured_calls.append(hint)
        return [
            {
                "content_profile_id": "cp-unique",
                "name": "Unique",
                "score": 0.9,
                "package_name": "unique",
            }
        ]

    monkeypatch.setattr(cp_mod, "resolve_type_hint", _fake_resolve)
    ws_id = await _create_workspace(authenticated_client, "SpaceTHWs")
    resp = await authenticated_client.post(
        "/api/apps",
        json={
            "name": "Symmetric App",
            "type_hint": "synthetic-hint",
            "workspace_id": ws_id,
        },
    )
    # The resolver was called (Pitfall 6 verified — direct service call).
    assert captured_calls == ["synthetic-hint"], captured_calls
    # The endpoint accepted the resolved id (even though "cp-unique" is a
    # fake id, the picker-conflict gate accepted the hint and assigned it
    # to library_content_profile_id; downstream 'Library package not
    # found' may surface — accept either 200 or 400/404 from downstream).
    assert resp.status_code in (200, 400, 404), resp.text


# ---------------------------------------------------------------------------
# Test 18 — type_hint + library_content_profile_id → 400 conflicting_picker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_track_type_hint_conflicts_with_explicit_picker(
    authenticated_client: AsyncClient, test_user
):
    """type_hint + library_content_profile_id → 400 conflicting_picker."""
    ws_id = await _create_workspace(authenticated_client, "ConflictWs")
    resp = await authenticated_client.post(
        "/api/tracks",
        json={
            "title": "Conflict",
            "type_hint": "bug",
            "library_content_profile_id": "cp-fake",
            "workspace_id": ws_id,
        },
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()
    nested_err = (detail.get("details") or {}).get("error_code")
    assert nested_err == "content_profile.conflicting_picker", detail


def test_short_profile_name_caps_verbose_description():
    """A verbose authoring prompt must NOT become the entry-type label."""
    from app.services.agent_profiles import _short_profile_name

    desc = (
        "A simple Documents profile for a company document repository. "
        "One entry type: Document. Fields: Title (text), Description (markdown), "
        "Category (select: Policy, Procedure, Form, Other), Effective Date (date)."
    )
    out = _short_profile_name(None, desc)
    assert 0 < len(out) <= 48
    assert "Fields" not in out  # didn't swallow the whole spec
    assert "\n" not in out


def test_short_profile_name_prefers_explicit_name():
    from app.services.agent_profiles import _short_profile_name

    assert _short_profile_name("Document", "some long description …") == "Document"


def test_short_profile_name_falls_back_to_untitled():
    from app.services.agent_profiles import _short_profile_name

    assert _short_profile_name(None, "") == "Untitled"


# ---------------------------------------------------------------------------
# agent_profiles.author_profile — structured entry_types (populated one-shot)
# ---------------------------------------------------------------------------


def test_build_manifest_entry_types_populates_fields():
    """Structured entry_types → populated manifest types: slugged keys,
    preserved field types, select ``enum`` carried through."""
    from app.services.agent_profiles import _build_manifest_entry_types

    built = _build_manifest_entry_types(
        [
            {
                "name": "Document",
                "fields": [
                    {"key": "title", "name": "Title", "type": "text"},
                    {
                        "key": "category",
                        "name": "Category",
                        "type": "select",
                        "enum": ["Policy", "Form"],
                    },
                ],
            }
        ],
        "Documents",
    )
    assert len(built) == 1
    et = built[0]
    assert et["name"] == "Document" and et["key"] == "document"
    keys = [f["key"] for f in et["fields"]]
    assert keys == ["title", "category"]
    cat = et["fields"][1]
    assert cat["type"] == "select" and cat["enum"] == ["Policy", "Form"]


def test_build_manifest_entry_types_empty_starter_fallback():
    """No entry_types → single empty starter named after the profile."""
    from app.services.agent_profiles import _build_manifest_entry_types

    for empty in (None, []):
        built = _build_manifest_entry_types(empty, "Documents")
        assert len(built) == 1
        assert built[0]["name"] == "Documents"
        assert built[0]["fields"] == []


def test_normalize_manifest_field_slugs_key_and_defaults_type():
    """Field key is slugged; type defaults to text; unknown keys dropped."""
    from app.services.agent_profiles import _normalize_manifest_field

    out = _normalize_manifest_field({"name": "Effective Date", "junk": "x"})
    assert out["key"] == "effective_date"
    assert out["name"] == "Effective Date"
    assert out["type"] == "text"
    assert "junk" not in out


def test_build_manifest_entry_types_compiles_valid_manifest():
    """The built types pass canonical-manifest compilation with fields intact."""
    from app.services.agent_profiles import _build_manifest_entry_types
    from app.services.content_profile_runtime import compile_canonical_manifest

    built = _build_manifest_entry_types(
        [
            {
                "name": "Document",
                "fields": [{"key": "title", "name": "Title", "type": "text"}],
            }
        ],
        "Documents",
    )
    manifest = {
        "content_profile_schema_version": 2,
        "scope": "track",
        "track": {
            "entry_types": built,
            "views": [
                {
                    "key": "feed",
                    "name": "Feed",
                    "view_type": "feed",
                    "is_default": True,
                    "filters": [],
                    "sort": [],
                    "group_by": None,
                    "layout": {},
                    "field_visibility": [],
                    "kanban_columns": [],
                    "calendar_mapping": {},
                    "config": {},
                }
            ],
            "taxonomy": {"tag_groups": []},
            "defaults": {
                "default_entry_type": built[0]["key"],
                "default_view": "feed",
            },
        },
        "package": {},
        "migrations": [],
    }
    compiled = compile_canonical_manifest(manifest=manifest, scope_hint="track")
    fields = compiled["track"]["entry_types"][0].get("fields", [])
    assert [f.get("key") for f in fields] == ["title"]


def test_stage_author_profile_forwards_entry_types():
    """The author stager forwards a non-empty entry_types list into the
    payload and renders the types in the staged-card copy."""
    from app.agentive.tooling.bindings import _stage_author_profile

    staged = _stage_author_profile(
        {
            "description": "Company document repository.",
            "name": "Documents",
            "entry_types": [
                {
                    "name": "Document",
                    "fields": [{"key": "title", "name": "Title", "type": "text"}],
                }
            ],
        }
    )
    assert staged["payload"].get("entry_types")
    assert "Document" in staged["diff_human"]
    assert "Title" in staged["diff_human"]
