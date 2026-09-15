"""Tests for the structural diff service (Pillar 2).

Phase 5 Plan 05-04 extends this file with HTTP-level coverage for the
``POST /api/content-profiles/{id}/preview-update`` alias (MIG-04) and the
``would_need_migration`` + ``unhandled_breaks`` payload extensions plumbed
through the shared ``diff_content_profile`` handler.
"""

import pytest
from httpx import AsyncClient

from app.services.content_profile_diff import compute_manifest_diff

# Guard: Plan 05-04 imports detect_unhandled_breaks from Plan 05-02's
# reject_gate module. If 05-02 has not landed at execute time, fail fast
# so the missing dependency is obvious.
from app.services.migrations.reject_gate import detect_unhandled_breaks  # noqa: F401


def _track_manifest(entry_types=None, views=None, taxonomy=None, field_types=None):
    out = {
        "scope": "track",
        "track": {
            "entry_types": list(entry_types or []),
            "views": list(views or []),
            "taxonomy": taxonomy or {"tag_groups": []},
        },
    }
    if field_types:
        out["field_types"] = list(field_types)
    return out


def test_diff_added_entry_type():
    before = _track_manifest()
    after = _track_manifest(entry_types=[{"key": "task", "name": "Task", "fields": []}])
    diff = compute_manifest_diff(before, after)
    assert diff["scope"] == "track"
    assert len(diff["entry_types"]["added"]) == 1
    assert diff["entry_types"]["added"][0]["key"] == "task"
    assert diff["entry_types"]["removed"] == []


def test_diff_removed_entry_type():
    before = _track_manifest(
        entry_types=[{"key": "task", "name": "Task", "fields": []}]
    )
    after = _track_manifest()
    diff = compute_manifest_diff(before, after)
    assert len(diff["entry_types"]["removed"]) == 1
    assert diff["entry_types"]["removed"][0]["key"] == "task"


def test_diff_changed_entry_type_fields():
    before = _track_manifest(
        entry_types=[
            {
                "key": "task",
                "name": "Task",
                "fields": [{"key": "title", "type": "text"}],
            }
        ]
    )
    after = _track_manifest(
        entry_types=[
            {
                "key": "task",
                "name": "Task",
                "fields": [
                    {"key": "title", "type": "text"},
                    {"key": "priority", "type": "select", "enum": ["low"]},
                ],
            }
        ]
    )
    diff = compute_manifest_diff(before, after)
    changed = diff["entry_types"]["changed"]
    assert len(changed) == 1
    assert changed[0]["fields"]["added"][0]["key"] == "priority"


def test_diff_added_field_type_composite():
    before = _track_manifest()
    after = _track_manifest(field_types=[{"key": "currency", "base": "number"}])
    diff = compute_manifest_diff(before, after)
    assert len(diff["field_types"]["added"]) == 1
    assert diff["field_types"]["added"][0]["key"] == "currency"


def test_diff_changed_view_config():
    before = _track_manifest(
        views=[{"key": "board", "name": "Board", "view_type": "kanban"}]
    )
    after = _track_manifest(
        views=[
            {
                "key": "board",
                "name": "Board",
                "view_type": "kanban",
                "kanban_columns": [{"key": "todo"}],
            }
        ]
    )
    diff = compute_manifest_diff(before, after)
    assert len(diff["views"]["changed"]) == 1
    assert diff["views"]["changed"][0]["key"] == "board"


def test_diff_taxonomy_added_tag():
    before = _track_manifest()
    after = _track_manifest(
        taxonomy={"tag_groups": [{"key": "g", "tags": [{"key": "x", "name": "X"}]}]}
    )
    diff = compute_manifest_diff(before, after)
    assert len(diff["tags"]["added"]) == 1
    assert diff["tags"]["added"][0]["key"] == "x"
    assert diff["tags"]["added"][0]["_group_key"] == "g"


def test_diff_empty_inputs_safe():
    diff = compute_manifest_diff(None, None)
    # Default to track scope
    assert diff["scope"] == "track"
    assert diff["entry_types"]["added"] == []
    assert diff["views"]["added"] == []


def test_diff_app_scope_added_track():
    before = {"scope": "app", "app": {"tracks": [], "relations": []}}
    after = {
        "scope": "app",
        "app": {
            "tracks": [
                {
                    "key": "contacts",
                    "name": "Contacts",
                    "entry_types": [],
                    "views": [],
                    "taxonomy": {"tag_groups": []},
                }
            ],
            "relations": [],
        },
    }
    diff = compute_manifest_diff(before, after)
    assert diff["scope"] == "app"
    assert diff["tracks"]["added"][0]["key"] == "contacts"


# ---------------------------------------------------------------------------
# Phase 5 Plan 05-04 — MIG-04 preview-update alias + payload extensions.
# ---------------------------------------------------------------------------


async def _create_published_library_cp(
    authenticated_client: AsyncClient, *, with_field: bool = False
) -> dict:
    """Mirror the helper in test_content_profile_drafts.py.

    Creates a tiny single-EntryType library CP. When ``with_field=True``,
    the entry_type starts with one text field (so a draft can rename / drop
    it to drive an "unhandled_breaks" scenario without needing real
    entries in a track).
    """
    org_resp = await authenticated_client.post(
        "/api/workspaces", json={"name": "Preview-Update Org"}
    )
    assert org_resp.status_code == 200, org_resp.text
    org_id = org_resp.json()["workspace"]["id"]
    fields = [{"key": "title", "type": "text"}] if with_field else []
    manifest = {
        "content_profile_schema_version": 2,
        "scope": "track",
        "package": {"name": "preview-update-test"},
        "track": {
            "entry_types": [{"key": "task", "name": "Task", "fields": fields}],
            "views": [{"key": "feed", "name": "Feed", "view_type": "feed"}],
            "taxonomy": {"tag_groups": []},
        },
    }
    pub = await authenticated_client.post(
        "/api/content-profiles",
        json={
            "name": "Preview Update Pack",
            "workspace_id": org_id,
            "manifest": manifest,
        },
    )
    assert pub.status_code == 200, pub.text
    return pub.json()["content_profile"]


async def _fork_and_mutate_draft(
    authenticated_client: AsyncClient,
    published_id: str,
    *,
    add_entry_type: bool = False,
    migrations: list | None = None,
) -> dict:
    """Fork draft, mutate manifest, optionally attach a migrations list, PUT."""
    fork = await authenticated_client.post(
        f"/api/content-profiles/{published_id}/draft", json={}
    )
    assert fork.status_code == 200, fork.text
    draft = fork.json()["draft"]

    new_manifest = dict(draft["manifest"])
    if add_entry_type:
        new_manifest["track"]["entry_types"].append(
            {"key": "note", "name": "Note", "fields": []}
        )
    if migrations is not None:
        new_manifest["migrations"] = migrations

    upd = await authenticated_client.put(
        f"/api/content-profiles/{draft['id']}",
        json={"manifest": new_manifest},
    )
    assert upd.status_code == 200, upd.text
    return upd.json()["content_profile"]


@pytest.mark.asyncio
class TestPreviewUpdateAlias:
    """MIG-04 — POST /api/content-profiles/{id}/preview-update."""

    async def test_alias_responds_200_with_diff_payload_shape(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test 1: alias responds 200 with same payload shape as /diff."""
        published = await _create_published_library_cp(authenticated_client)
        draft = await _fork_and_mutate_draft(
            authenticated_client, published["id"], add_entry_type=True
        )
        resp = await authenticated_client.post(
            f"/api/content-profiles/{draft['id']}/preview-update",
            json={"include_entry_impact": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "candidate_id" in body
        assert "reference_id" in body
        assert "diff" in body
        added = [e["key"] for e in body["diff"]["entry_types"]["added"]]
        assert "note" in added

    async def test_alias_and_diff_return_identical_payloads(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test 2: /diff and /preview-update return identical bodies.

        With include_entry_impact=False, the two new top-level fields are
        absent on both, so the responses are byte-identical (modulo the
        candidate_id / reference_id which are stable across both calls).
        """
        published = await _create_published_library_cp(authenticated_client)
        draft = await _fork_and_mutate_draft(
            authenticated_client, published["id"], add_entry_type=True
        )
        diff_resp = await authenticated_client.post(
            f"/api/content-profiles/{draft['id']}/diff",
            json={"include_entry_impact": False},
        )
        preview_resp = await authenticated_client.post(
            f"/api/content-profiles/{draft['id']}/preview-update",
            json={"include_entry_impact": False},
        )
        assert diff_resp.status_code == 200
        assert preview_resp.status_code == 200
        assert diff_resp.json() == preview_resp.json()

    async def test_would_need_migration_false_for_additive_change(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test 4: additive-only manifest → would_need_migration=False.

        A pure entry_type addition with no live attached tracks yields
        entry_impact=[] from compute_entry_impact_for_attached, which rolls
        up to would_need_migration=False at the top level.
        """
        published = await _create_published_library_cp(authenticated_client)
        draft = await _fork_and_mutate_draft(
            authenticated_client, published["id"], add_entry_type=True
        )
        resp = await authenticated_client.post(
            f"/api/content-profiles/{draft['id']}/preview-update",
            json={"include_entry_impact": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["would_need_migration"] is False
        assert body["unhandled_breaks"] == []

    async def test_payload_extensions_absent_when_include_entry_impact_false(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test 5 + Test 8: extensions absent when include_entry_impact=False."""
        published = await _create_published_library_cp(authenticated_client)
        draft = await _fork_and_mutate_draft(
            authenticated_client, published["id"], add_entry_type=True
        )
        resp = await authenticated_client.post(
            f"/api/content-profiles/{draft['id']}/preview-update",
            json={"include_entry_impact": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Top-level extensions MUST NOT appear when entry_impact wasn't
        # computed — otherwise callers could read a stale-default value
        # that doesn't reflect reality.
        assert "would_need_migration" not in body
        assert "unhandled_breaks" not in body
        # The structural diff must still be present.
        assert "diff" in body

    async def test_404_when_cp_not_found(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test 12: bogus content_profile_id → 404."""
        resp = await authenticated_client.post(
            "/api/content-profiles/cp:does_not_exist/preview-update",
            json={},
        )
        assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Payload-extension rollup logic — unit-level coverage via monkeypatch.
#
# The HTTP path above proves the alias works end-to-end; these tests pin the
# would_need_migration rollup + unhandled_breaks delegation by injecting a
# synthetic impacts list (avoids the heavy lift of building a track with
# field-drifting entries just to exercise the rollup branch).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPreviewUpdatePayloadRollup:
    async def test_would_need_migration_true_when_any_impact_needs_migration(
        self, authenticated_client: AsyncClient, test_user, monkeypatch
    ):
        """Test 3: synthetic impacts list with would_need_migration>0 rolls up to True."""
        from app.services import content_profile_diff as diff_mod

        async def _fake_impacts(*, cp, candidate_manifest, sample_limit=20):
            return [
                {
                    "track_id": "trk:test",
                    "total": 5,
                    "would_fail_validation": 0,
                    "would_need_migration": 2,
                    "sample_failing_ids": [],
                    "sample_failure_reasons": [],
                }
            ]

        monkeypatch.setattr(
            diff_mod, "compute_entry_impact_for_attached", _fake_impacts
        )

        published = await _create_published_library_cp(
            authenticated_client, with_field=True
        )
        draft = await _fork_and_mutate_draft(authenticated_client, published["id"])
        resp = await authenticated_client.post(
            f"/api/content-profiles/{draft['id']}/preview-update",
            json={"include_entry_impact": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["would_need_migration"] is True
        # No declared migrations → unhandled_breaks non-empty
        assert isinstance(body["unhandled_breaks"], list)
        assert len(body["unhandled_breaks"]) >= 1
        assert "trk:test" in body["unhandled_breaks"][0]

    async def test_unhandled_breaks_empty_when_migration_declared(
        self, authenticated_client: AsyncClient, test_user, monkeypatch
    ):
        """Test 6: declared migrations[].ops[] → unhandled_breaks=[]."""
        from app.services import content_profile_diff as diff_mod

        async def _fake_impacts(*, cp, candidate_manifest, sample_limit=20):
            return [
                {
                    "track_id": "trk:test",
                    "total": 3,
                    "would_fail_validation": 0,
                    "would_need_migration": 1,
                    "sample_failing_ids": [],
                    "sample_failure_reasons": [],
                }
            ]

        monkeypatch.setattr(
            diff_mod, "compute_entry_impact_for_attached", _fake_impacts
        )

        published = await _create_published_library_cp(
            authenticated_client, with_field=True
        )
        # Attach a migrations[].ops[] entry so the reject_gate treats the
        # break as covered.
        migrations = [
            {
                "from_version": 1,
                "to_version": 2,
                "ops": [
                    {
                        "op": "rename_field",
                        "entry_type": "task",
                        "from": "title",
                        "to": "name",
                    }
                ],
            }
        ]
        draft = await _fork_and_mutate_draft(
            authenticated_client,
            published["id"],
            migrations=migrations,
        )
        resp = await authenticated_client.post(
            f"/api/content-profiles/{draft['id']}/preview-update",
            json={"include_entry_impact": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Author declared at least one migration op — v1 conservative reject
        # gate trusts the author; unhandled_breaks is empty.
        assert body["unhandled_breaks"] == []
        # would_need_migration rollup is still True (it reflects raw impact,
        # independent of whether migrations cover the break).
        assert body["would_need_migration"] is True

    async def test_unhandled_breaks_nonempty_when_no_migration_declared(
        self, authenticated_client: AsyncClient, test_user, monkeypatch
    ):
        """Test 7: would_need_migration impact + no migrations → unhandled_breaks non-empty."""
        from app.services import content_profile_diff as diff_mod

        async def _fake_impacts(*, cp, candidate_manifest, sample_limit=20):
            return [
                {
                    "track_id": "trk:abc",
                    "total": 10,
                    "would_fail_validation": 0,
                    "would_need_migration": 4,
                    "sample_failing_ids": [],
                    "sample_failure_reasons": [],
                }
            ]

        monkeypatch.setattr(
            diff_mod, "compute_entry_impact_for_attached", _fake_impacts
        )

        published = await _create_published_library_cp(authenticated_client)
        draft = await _fork_and_mutate_draft(
            authenticated_client, published["id"], add_entry_type=True
        )
        # No migrations declared on the draft manifest.
        resp = await authenticated_client.post(
            f"/api/content-profiles/{draft['id']}/preview-update",
            json={"include_entry_impact": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["would_need_migration"] is True
        assert len(body["unhandled_breaks"]) >= 1
        # Descriptor contains the track id surfaced by detect_unhandled_breaks
        joined = " ".join(body["unhandled_breaks"])
        assert "trk:abc" in joined
        assert "migration" in joined.lower()


# ---------------------------------------------------------------------------
# Back-compat / hygiene gates.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_diff_endpoint_payload_extensions_apply(
    authenticated_client: AsyncClient, test_user
):
    """Test 9 (back-compat): existing /diff endpoint receives the same payload
    extensions (since alias and /diff share one handler), but the existing
    test suite continues to pass UNCHANGED — extensions are ADDITIVE."""
    published = await _create_published_library_cp(authenticated_client)
    draft = await _fork_and_mutate_draft(
        authenticated_client, published["id"], add_entry_type=True
    )
    resp = await authenticated_client.post(
        f"/api/content-profiles/{draft['id']}/diff",
        json={"include_entry_impact": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # New top-level fields land on /diff too (single handler — single payload
    # shape). Library CP has no attached tracks so the rollup is False.
    assert body["would_need_migration"] is False
    assert body["unhandled_breaks"] == []
    # Original structural diff is unchanged.
    assert "diff" in body
    assert "candidate_id" in body
    assert "reference_id" in body


@pytest.mark.asyncio
async def test_unauthenticated_preview_update_rejected(client: AsyncClient):
    """Test 10: unauthenticated request to /preview-update → 401."""
    resp = await client.post(
        "/api/content-profiles/cp:anything/preview-update", json={}
    )
    # Without TestAuthBypassMiddleware injection (client fixture is anon)
    # the request hits the auth gate. Some auth paths return 401, some 403
    # depending on how the request lands — both prove the gate fired.
    assert resp.status_code in (401, 403), resp.text


def test_single_literal_grep_gates_unchanged():
    """Test 13: PolicyAction / ChangeEventAction / ActorKind single-Literal
    grep gates return exactly 1 — Plan 05-04 adds ZERO Literal members."""
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    backend_app = repo_root / "backend" / "app"
    for symbol in ("PolicyAction", "ChangeEventAction", "ActorKind"):
        # Python ``re`` keeps the gate portable across GNU grep builds that
        # do not treat ``\\s`` as whitespace.
        rx = re.compile(rf"^{symbol}\s*=\s*Literal")
        matches: list[str] = []
        for py in backend_app.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if rx.search(line):
                    matches.append(f"{py.as_posix()}:{i}:{line}")
        assert len(matches) == 1, (
            f"{symbol} should have exactly 1 Literal declaration, "
            f"found {len(matches)}: {matches}"
        )
