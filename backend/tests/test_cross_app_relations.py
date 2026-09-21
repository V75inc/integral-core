"""End-to-end cross-App relations — provider + consumer apps (substrate).

Phase 10 Plan 10-06 (APP-CROSS-RELATIONS-01) — ROADMAP Phase 10 Cross-App gate.

This test file is the proving ground for the full substrate:
  - Install order enforcement via requires_apps[].
  - Edge-level walk during uninstall (REFERENCES.target_app_id).
  - on_target_uninstall cascade policies (block / null / archive_self).
  - I-APP-05 same-Workspace gate.
  - Multi-install resolution (workspace ambiguity / instance pin).
  - I-APP-03 restricted-stub leak guarantee.

Includes the grep-gate test for the ``label_field`` single-source enforcement.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict

import pytest

from app.exceptions import (
    AmbiguousCrossAppTargetError,
    AppUninstallBlockedError,
    BadRequestError,
    CrossWorkspaceTargetRejectedError,
)
from app.models.edges import CONTAINS, REFERENCES
from app.models.nodes import App, Entry, OperationalModel, Track, Workspace
from app.schemas.policy import Subject
from app.services.app_lifecycle import install_app, uninstall_app
from app.services.relation_runtime import (
    materialize_cross_app_reference,
    read_cross_app_label,
    resolve_target_app,
)
from app.services.walkers.cross_app_resolver import find_inbound_references
from app.utils.time import utc_now_iso
from tests.fixtures.workspaces import make_org_workspace

# ---------------------------------------------------------------------------
# App manifest factories — minimal but realistic
# ---------------------------------------------------------------------------


def _provider_manifest() -> Dict[str, Any]:
    """Provider app — declares an ``employees`` track of record entries."""
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {
            "name": "provider_app",
            "key": "provider_app",
            "version": "1.0.0",
            "description": "Provider App",
            "tags": ["test"],
        },
        "app": {
            "description": "Provider App",
            "tracks": [
                {
                    "key": "employees",
                    "name": "Employees",
                    "provision_on_create": True,
                    "entry_types": [
                        {
                            "key": "employee",
                            "name": "Employee",
                            "fields": [
                                {"key": "name", "name": "Name", "type": "text"},
                                {"key": "role", "name": "Role", "type": "text"},
                            ],
                        }
                    ],
                    "views": [{"key": "feed", "name": "Feed", "type": "feed"}],
                }
            ],
        },
    }


def _consumer_manifest(
    on_target_uninstall: str = "block", hard_requires_provider: bool = True
) -> Dict[str, Any]:
    """Consumer app — references provider records via cross-App relation.

    ``hard_requires_provider`` toggles the manifest-level dep. The default
    (True) is the realistic shape. Cascade tests (on_target_uninstall=null/
    archive_self) use the soft-dep variant so the manifest-level walk does
    not preempt edge-level cascade behavior.
    """
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {
            "name": "consumer_app",
            "key": "consumer_app",
            "version": "1.0.0",
            "description": "Consumer App",
            "tags": ["test"],
        },
        "app": {
            "description": "Consumer App",
            "requires_apps": [
                {
                    "key": "provider_app",
                    "min_version": "1.0.0",
                    "optional": not hard_requires_provider,
                }
            ],
            "tracks": [
                {
                    "key": "dependent_records",
                    "name": "Dependent Records",
                    "provision_on_create": True,
                    "entry_types": [
                        {
                            "key": "pay_run",
                            "name": "Pay Run",
                            "fields": [
                                {"key": "period", "name": "Period", "type": "text"},
                                {
                                    "key": "employees",
                                    "name": "Employees",
                                    "type": "relation",
                                    "relation": {
                                        "target": "entry",
                                        "many": True,
                                        "target_app": "provider_app",
                                        "allow_cross_app": True,
                                        "label_field": "title",
                                        "on_target_uninstall": on_target_uninstall,
                                        "resolution": "workspace",
                                    },
                                },
                            ],
                        }
                    ],
                    "views": [{"key": "feed", "name": "Feed", "type": "feed"}],
                }
            ],
        },
    }


def _downstream_manifest() -> Dict[str, Any]:
    """Second downstream app — also references provider records."""
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {
            "name": "downstream_app",
            "key": "downstream_app",
            "version": "1.0.0",
            "description": "Downstream App",
            "tags": ["test"],
        },
        "app": {
            "description": "Downstream App",
            "requires_apps": [
                {"key": "provider_app", "min_version": "1.0.0", "optional": False}
            ],
            "tracks": [
                {
                    "key": "reviews",
                    "name": "Reviews",
                    "provision_on_create": True,
                    "entry_types": [
                        {
                            "key": "review",
                            "name": "Review",
                            "fields": [
                                {
                                    "key": "subject",
                                    "name": "Subject",
                                    "type": "relation",
                                    "relation": {
                                        "target": "entry",
                                        "many": False,
                                        "target_app": "provider_app",
                                        "allow_cross_app": True,
                                        "label_field": "title",
                                        "on_target_uninstall": "block",
                                        "resolution": "workspace",
                                    },
                                },
                                {"key": "rating", "name": "Rating", "type": "text"},
                            ],
                        }
                    ],
                    "views": [{"key": "feed", "name": "Feed", "type": "feed"}],
                }
            ],
        },
    }


async def _make_workspace(name: str = "ws-cross-app") -> Workspace:
    return await make_org_workspace(name)


async def _make_library_cp(manifest: Dict[str, Any]) -> OperationalModel:
    now = utc_now_iso()
    return await OperationalModel.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version=manifest["package"].get("version") or "1.0.0",
        created_at=now,
        updated_at=now,
    )


async def _install(ws_id: str, manifest: Dict[str, Any]) -> str:
    lib = await _make_library_cp(manifest)
    result = await install_app(workspace_id=ws_id, library_cp_id=lib.id, actor_id="u_1")
    assert result["status"] == "active"
    return result["app_id"]


async def _install_additional_copy(ws_id: str, manifest: Dict[str, Any]) -> str:
    """Create a second active App for the same bundle key (multi-install probe).

    ``install_app`` dedupes by bundle identity and returns the canonical row;
    cross-App resolution tests need two distinct active installs to exercise
    workspace ambiguity and instance pinning.
    """
    from app.services.app_graph import (
        catalog_app,
        get_app_attached_operational_model,
        wire_app_owner,
    )
    from app.services.operational_model_merge import (
        merge_library_manifest_into_operational_model,
    )

    lib = await _make_library_cp(manifest)
    now = utc_now_iso()
    pkg = manifest["package"]
    app = await App.create(
        name=pkg["name"],
        name_fold=pkg["name"].casefold(),
        owner_user_id="u_1",
        workspace_id=ws_id,
        description=pkg.get("description") or "",
        visibility="private",
        lifecycle_state="active",
        installed_from_library_id=lib.id,
        source_operational_model_slug=pkg.get("key") or pkg["name"],
        version=pkg.get("version"),
        installed_at=now,
        created_at=now,
        updated_at=now,
    )
    await wire_app_owner(app, "u_1", workspace_id=ws_id)
    await catalog_app(app)
    attached_cp = await get_app_attached_operational_model(app)
    if attached_cp:
        await merge_library_manifest_into_operational_model(
            lib, attached_cp, track=None, for_space=True
        )
    return app.id


async def _make_entry_in_app(
    app_id: str,
    track_key: str,
    title: str,
    custom_fields: Dict[str, Any] | None = None,
) -> Entry:
    """Find the track by key under the App and create an entry."""
    app_node = await App.get(app_id)
    assert app_node is not None
    tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"])
    target_track = None
    for tr in tracks:
        if getattr(tr, "template_id", "") == track_key or (
            tr.title_fold == track_key.casefold()
        ):
            target_track = tr
            break
    assert target_track is not None, f"Track {track_key!r} not found in App {app_id}"
    now = utc_now_iso()
    entry = await Entry.create(
        title=title,
        body="",
        tags=[],
        custom_fields=custom_fields or {},
        track_id=target_track.id,
        author_id="u_1",
        status="active",
        created_at=now,
        updated_at=now,
    )
    await target_track.connect(entry, edge=CONTAINS, added_at=now)
    return entry


# ---------------------------------------------------------------------------
# Install ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_order_provider_then_consumer_succeeds():
    ws = await _make_workspace()
    await _install(ws.id, _provider_manifest())
    await _install(ws.id, _consumer_manifest())


@pytest.mark.asyncio
async def test_install_consumer_first_blocked():
    ws = await _make_workspace()
    from app.exceptions import AppDependencyError

    with pytest.raises(AppDependencyError):
        await _install(ws.id, _consumer_manifest())


# ---------------------------------------------------------------------------
# Uninstall — edge-level walk catches stored REFERENCES.target_app_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uninstall_blocked_by_inbound_cross_app_references():
    """Provider uninstall blocked when a consumer entry references a provider record."""
    ws = await _make_workspace()
    provider_app_id = await _install(ws.id, _provider_manifest())
    consumer_app_id = await _install(ws.id, _consumer_manifest("block"))
    employee = await _make_entry_in_app(provider_app_id, "employees", "Eldon Marks")
    pay_run = await _make_entry_in_app(consumer_app_id, "dependent_records", "2026-Q1")

    # Materialize the cross-App reference via the runtime helper.
    await materialize_cross_app_reference(
        source_entry=pay_run,
        field_key="employees",
        target_entry=employee,
        target_app_id=provider_app_id,
    )

    # Sanity — the inbound walker sees the reference.
    refs = await find_inbound_references(
        target_app_id=provider_app_id, workspace_id=ws.id
    )
    assert len(refs) == 1
    assert refs[0]["source_entry_id"] == pay_run.id
    assert refs[0]["relation_field_key"] == "employees"

    # Uninstall provider (block policy on the source field) — must 409.
    with pytest.raises(AppUninstallBlockedError) as exc_info:
        await uninstall_app(app_id=provider_app_id, actor_id="u_1")
    details = exc_info.value.details
    blocking_refs = details.get("blocking_references", [])
    assert len(blocking_refs) == 1
    assert blocking_refs[0]["source_entry_id"] == pay_run.id
    assert blocking_refs[0]["relation_field_key"] == "employees"


@pytest.mark.asyncio
async def test_uninstall_force_succeeds_with_reference_overrides_in_details():
    ws = await _make_workspace()
    provider_app_id = await _install(ws.id, _provider_manifest())
    consumer_app_id = await _install(ws.id, _consumer_manifest("block"))
    employee = await _make_entry_in_app(provider_app_id, "employees", "Eldon")
    pay_run = await _make_entry_in_app(consumer_app_id, "dependent_records", "2026-Q1")
    await materialize_cross_app_reference(
        source_entry=pay_run,
        field_key="employees",
        target_entry=employee,
        target_app_id=provider_app_id,
    )
    out = await uninstall_app(app_id=provider_app_id, actor_id="u_1", force=True)
    assert out["status"] == "force_uninstalled"


# ---------------------------------------------------------------------------
# on_target_uninstall = null — source ref cleared + entry.update emitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uninstall_null_cascade_clears_source_field():
    ws = await _make_workspace()
    provider_app_id = await _install(ws.id, _provider_manifest())
    # Soft manifest dep so manifest-level walk doesn't preempt the cascade.
    consumer_app_id = await _install(
        ws.id, _consumer_manifest("null", hard_requires_provider=False)
    )
    employee = await _make_entry_in_app(provider_app_id, "employees", "Eldon")
    pay_run = await _make_entry_in_app(
        consumer_app_id,
        "dependent_records",
        "2026-Q1",
        custom_fields={"employees": [employee.id]},
    )
    await materialize_cross_app_reference(
        source_entry=pay_run,
        field_key="employees",
        target_entry=employee,
        target_app_id=provider_app_id,
    )

    # Uninstall provider — null policy clears the source field.
    out = await uninstall_app(app_id=provider_app_id, actor_id="u_1")
    assert out["status"] == "uninstalled"

    refreshed = await Entry.get(pay_run.id)
    assert refreshed is not None
    assert refreshed.custom_fields.get("employees") == []


# ---------------------------------------------------------------------------
# on_target_uninstall = archive_self — source entry archived
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uninstall_archive_self_cascade_archives_source_entry():
    ws = await _make_workspace()
    provider_app_id = await _install(ws.id, _provider_manifest())
    consumer_app_id = await _install(
        ws.id, _consumer_manifest("archive_self", hard_requires_provider=False)
    )
    employee = await _make_entry_in_app(provider_app_id, "employees", "Eldon")
    pay_run = await _make_entry_in_app(
        consumer_app_id,
        "dependent_records",
        "2026-Q1",
        custom_fields={"employees": [employee.id]},
    )
    await materialize_cross_app_reference(
        source_entry=pay_run,
        field_key="employees",
        target_entry=employee,
        target_app_id=provider_app_id,
    )

    out = await uninstall_app(app_id=provider_app_id, actor_id="u_1")
    assert out["status"] == "uninstalled"

    refreshed = await Entry.get(pay_run.id)
    assert refreshed is not None
    assert refreshed.status == "archived"


# ---------------------------------------------------------------------------
# I-APP-05 — same-Workspace gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_workspace_target_rejected_via_instance_pin():
    ws_a = await _make_workspace(name="ws-a")
    ws_b = await _make_workspace(name="ws-b")
    # Install provider in workspace B.
    provider_in_b_id = await _install(ws_b.id, _provider_manifest())
    # From workspace A, attempt to resolve the provider app pinned to its
    # workspace-B id — must be rejected.
    with pytest.raises(CrossWorkspaceTargetRejectedError):
        await resolve_target_app(
            workspace_id=ws_a.id,
            target_app_key="provider_app",
            resolution=f"instance:{provider_in_b_id}",
        )


# ---------------------------------------------------------------------------
# Multi-install resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_install_resolution_workspace_ambiguous():
    """workspace resolution errors when multiple provider installs exist."""
    ws = await _make_workspace()
    await _install(ws.id, _provider_manifest())
    provider_v2 = _provider_manifest()
    provider_v2["package"]["version"] = "1.0.1"
    await _install_additional_copy(ws.id, provider_v2)
    with pytest.raises(AmbiguousCrossAppTargetError):
        await resolve_target_app(
            workspace_id=ws.id,
            target_app_key="provider_app",
            resolution="workspace",
        )


@pytest.mark.asyncio
async def test_multi_install_resolution_instance_pins():
    ws = await _make_workspace()
    provider1_id = await _install(ws.id, _provider_manifest())
    provider_v2 = _provider_manifest()
    provider_v2["package"]["version"] = "1.0.1"
    provider2_id = await _install_additional_copy(ws.id, provider_v2)
    resolved = await resolve_target_app(
        workspace_id=ws.id,
        target_app_key="provider_app",
        resolution=f"instance:{provider2_id}",
    )
    assert resolved == provider2_id
    assert resolved != provider1_id


# ---------------------------------------------------------------------------
# I-APP-03 — restricted-stub leak guarantee (the THE critical test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restricted_stub_carries_no_label_field_value():
    """The single data-leak vector — denial returns zero source-field content."""
    ws = await _make_workspace()
    provider_app_id = await _install(ws.id, _provider_manifest())
    employee = await _make_entry_in_app(
        provider_app_id, "employees", "SECRET_EMPLOYEE_NAME"
    )

    # Connector viewer with no HAS_POLICY edge → fail_closed_no_policy.
    viewer = Subject(kind="connector", id="conn_anonymous")
    out = await read_cross_app_label(
        viewer=viewer,
        relation_field_key="employees",
        target_entry_id=employee.id,
        target_app_id=provider_app_id,
        label_field="title",
    )

    # STRICT shape — three keys, no more.
    assert set(out.keys()) == {
        "kind",
        "relation_field_key",
        "target_resource_kind",
    }, f"Restricted stub leaked extra keys: {set(out.keys())}"
    assert out["kind"] == "restricted_relation"
    assert out["target_resource_kind"] == "entry"
    assert out["relation_field_key"] == "employees"
    # Negative: title MUST NOT appear anywhere in the stub.
    assert "SECRET_EMPLOYEE_NAME" not in str(out)
    # Negative: target id / app id / app name must not appear.
    assert employee.id not in str(out)
    assert provider_app_id not in str(out)
    assert "provider_app" not in str(out)


@pytest.mark.asyncio
async def test_label_rendered_when_viewer_has_access():
    """System subject — bypass evaluation → full label rendered."""
    ws = await _make_workspace()
    provider_app_id = await _install(ws.id, _provider_manifest())
    employee = await _make_entry_in_app(provider_app_id, "employees", "Eldon Marks")
    viewer = Subject(kind="system", id="system_test")
    out = await read_cross_app_label(
        viewer=viewer,
        relation_field_key="employees",
        target_entry_id=employee.id,
        target_app_id=provider_app_id,
        label_field="title",
    )
    assert out["kind"] == "relation"
    assert out["label"] == "Eldon Marks"
    assert out["target_entry_id"] == employee.id
    assert out["target_app_id"] == provider_app_id


# ---------------------------------------------------------------------------
# Second downstream app referencing provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_app_dependency_chain_blocks_provider_uninstall_via_both():
    """Provider is referenced by consumer and downstream apps — uninstall sees both."""
    ws = await _make_workspace()
    provider_app_id = await _install(ws.id, _provider_manifest())
    await _install(ws.id, _consumer_manifest("block"))
    await _install(ws.id, _downstream_manifest())
    with pytest.raises(AppUninstallBlockedError) as exc_info:
        await uninstall_app(app_id=provider_app_id, actor_id="u_1")
    deps = exc_info.value.details.get("blocking_dependents", [])
    # Both consumer and downstream declare provider_app as a hard dep.
    dep_app_names = {d["app_name"] for d in deps}
    assert "consumer_app" in dep_app_names
    assert "downstream_app" in dep_app_names


# ---------------------------------------------------------------------------
# Single-source grep gate — label_field reads outside relation_runtime.py forbidden
# ---------------------------------------------------------------------------


def test_label_field_single_source_grep_gate():
    """Risk 4 / Pitfall 5 — ``label_field`` content reads must live in relation_runtime.

    Greps backend/app/ for ``label_field`` references. Allowed locations:
      - app/services/relation_runtime.py (the single safe reader)
      - app/services/operational_model_runtime.py (compile-time validator —
        sets the field on the canonical spec; does NOT read source-field
        content)
      - app/services/operational_model_compile.py (compile-time normalization
        split out of operational_model_runtime.py per
        .planning/refactors/operational_model_runtime_split_plan.md; same
        compile-time validator role — sets the field on the canonical spec,
        does NOT read source-field content)
      - app/schemas/cross_app_relations.py (schema definitions)

    Any other occurrence is a potential leak vector. Tests + schemas + the
    runtime resolver are the canonical owners.
    """
    repo_root = Path(__file__).resolve().parent.parent  # backend/
    cmd = [
        "grep",
        "-rln",
        "-E",
        r"\blabel_field\b",
        "app/",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root))
    hits = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and "__pycache__" not in line
    ]
    # Whitelist of files that are allowed to mention `label_field`. The
    # edges.py mention is a docstring reference to ``relation_runtime`` (the
    # safe reader); it does NOT touch field content.
    allowed_suffixes = {
        "app/services/relation_runtime.py",
        "app/services/operational_model_runtime.py",
        "app/services/operational_model_compile.py",
        "app/schemas/cross_app_relations.py",
        "app/models/edges.py",
    }
    forbidden = []
    for path in hits:
        normalized = path.lstrip("./")
        if any(normalized.endswith(s) for s in allowed_suffixes):
            continue
        forbidden.append(normalized)
    assert not forbidden, (
        f"Forbidden ``label_field`` references found outside the single-source "
        f"resolver: {forbidden}. Either route the read through "
        f"relation_runtime.read_cross_app_label OR add the file to the "
        f"whitelist in this test (with security review)."
    )
