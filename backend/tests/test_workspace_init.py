"""Phase D3: workspace-scope strict-init service.

Tests ``init_workspace_from_profile`` — the single-transactional entry
point that provisions every App declared by a scope=workspace bundle,
appends to ``Workspace.applied_profiles``, and invalidates the skill
scope cache.

Pre-flight validation (no writes):
  - Bundle is loaded from ``load_library_operational_models``.
  - Bundle ``manifest.scope == "workspace"``; otherwise raises
    ``WorkspaceInitValidationError``.
  - Slug is not already present in ``workspace.applied_profiles``;
    otherwise raises ``WorkspaceInitConflict``.

Happy path:
  - One App per ``workspace.apps[]`` entry, each carrying
    ``source_operational_model_slug = bundle_slug``.
  - ``workspace.applied_profiles`` grows by one ``{slug, version, applied_at}``
    entry.

Mid-write failure semantics (rollback) are exercised in the broader
suite at the API-endpoint layer (D4); this module focuses on the
service contract.
"""

from pathlib import Path

import pytest

from tests.fixtures.workspaces import make_org_workspace


@pytest.mark.asyncio
async def test_init_rejects_non_workspace_scope_bundle(monkeypatch):
    """A bundle whose manifest.scope is anything other than "workspace"
    must be rejected before any write occurs."""
    from app.models.nodes import Workspace
    from app.services.operational_model_loader import LibraryProfileSpec
    from app.services.operational_model_workspace_init import (
        WorkspaceInitValidationError,
        init_workspace_from_profile,
    )

    monkeypatch.setattr(
        "app.services.operational_model_loader.load_library_operational_models",
        lambda: [
            LibraryProfileSpec(
                name="A",
                slug="x",
                version="1.0.0",
                description="",
                manifest={"scope": "app"},
                scope="platform",
                library_package=True,
                bundle_dir=Path("/x"),
            )
        ],
    )
    ws = await make_org_workspace(name="W", kind="organization")
    with pytest.raises(WorkspaceInitValidationError):
        await init_workspace_from_profile(ws, "x", actor_id="u1")


@pytest.mark.asyncio
async def test_init_rejects_already_applied_slug(monkeypatch):
    """If the workspace's ``applied_profiles`` already contains an entry
    with this slug, the service raises ``WorkspaceInitConflict`` without
    making any writes."""
    from app.models.nodes import Workspace
    from app.services.operational_model_loader import LibraryProfileSpec
    from app.services.operational_model_workspace_init import (
        WorkspaceInitConflict,
        init_workspace_from_profile,
    )

    monkeypatch.setattr(
        "app.services.operational_model_loader.load_library_operational_models",
        lambda: [
            LibraryProfileSpec(
                name="X",
                slug="x",
                version="1.0.0",
                description="",
                manifest={
                    "scope": "workspace",
                    "workspace": {
                        "apps": [
                            {
                                "slug": "a",
                                "name": "A",
                                "profile": {"scope": "app", "app": {"tracks": []}},
                            }
                        ]
                    },
                },
                scope="platform",
                library_package=True,
                bundle_dir=Path("/x"),
            )
        ],
    )
    ws = await make_org_workspace(
        name="Y",
        kind="organization",
        applied_profiles=[{"slug": "x", "version": "1.0.0", "applied_at": "now"}],
    )
    with pytest.raises(WorkspaceInitConflict):
        await init_workspace_from_profile(ws, "x", actor_id="u1")


@pytest.mark.asyncio
async def test_init_creates_apps_and_writes_applied_profiles(monkeypatch):
    """Happy path: an App is created per ``workspace.apps[]`` entry, and
    ``applied_profiles`` is appended with the new slug."""
    from app.models.nodes import Workspace
    from app.services.operational_model_loader import LibraryProfileSpec
    from app.services.operational_model_workspace_init import (
        init_workspace_from_profile,
    )

    monkeypatch.setattr(
        "app.services.operational_model_loader.load_library_operational_models",
        lambda: [
            LibraryProfileSpec(
                name="Z",
                slug="z",
                version="1.0.0",
                description="",
                manifest={
                    "scope": "workspace",
                    "workspace": {
                        "apps": [
                            {
                                "slug": "crm",
                                "name": "CRM",
                                "profile": {
                                    "scope": "app",
                                    "app": {
                                        "tracks": [
                                            {
                                                "key": "contacts",
                                                "name": "Contacts",
                                                "entry_types": [],
                                            }
                                        ]
                                    },
                                },
                            }
                        ]
                    },
                },
                scope="platform",
                library_package=True,
                bundle_dir=Path("/z"),
            )
        ],
    )
    ws = await make_org_workspace(name="Z-ws", kind="organization")
    result = await init_workspace_from_profile(ws, "z", actor_id="u1")
    assert len(result.apps_created) == 1
    ws2 = await Workspace.get(ws.id)
    assert ws2 is not None
    assert any(e.get("slug") == "z" for e in ws2.applied_profiles)

    # G1: verify the sub-manifest actually materialized tracks under the App.
    from app.models.edges import CONTAINS
    from app.models.nodes import App

    app = await App.get(result.apps_created[0])
    assert app is not None
    contained_tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    assert (
        len(contained_tracks) >= 1
    ), f"expected >=1 Track materialized under App, got {contained_tracks}"


@pytest.mark.asyncio
async def test_init_rollback_does_not_attribute_error(monkeypatch):
    """Mid-write failure must trigger app.delete() (not the non-existent app.destroy())."""
    from app.models.nodes import Workspace
    from app.services.operational_model_loader import LibraryProfileSpec
    from app.services.operational_model_workspace_init import (
        WorkspaceInitFailed,
        init_workspace_from_profile,
    )

    monkeypatch.setattr(
        "app.services.operational_model_loader.load_library_operational_models",
        lambda: [
            LibraryProfileSpec(
                name="R",
                slug="r",
                version="1.0.0",
                description="",
                manifest={
                    "scope": "workspace",
                    "workspace": {
                        "apps": [
                            {
                                "slug": "a1",
                                "name": "A1",
                                "profile": {"scope": "app", "app": {"tracks": []}},
                            },
                            {
                                "slug": "a2",
                                "name": "A2",
                                "profile": {"scope": "app", "app": {"tracks": []}},
                            },
                        ]
                    },
                },
                scope="platform",
                library_package=True,
                bundle_dir=Path("/r"),
            )
        ],
    )

    # Force the second app's sub-manifest application to fail so the first
    # must roll back via app.delete().
    import app.services.operational_model_workspace_init as wsinit

    call_count = {"n": 0}
    orig_apply = wsinit._apply_app_submanifest

    async def fail_second(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("synthetic mid-write failure")
        return await orig_apply(*args, **kwargs)

    monkeypatch.setattr(wsinit, "_apply_app_submanifest", fail_second)

    ws = await make_org_workspace(name="rollback-test", kind="organization")
    with pytest.raises(WorkspaceInitFailed):
        await init_workspace_from_profile(ws, "r", actor_id="u1")
    # Must NOT raise AttributeError. The fact that we got WorkspaceInitFailed
    # means rollback ran without crashing.


@pytest.mark.asyncio
async def test_init_handles_cross_app_relations(monkeypatch):
    """G4: a workspace bundle declaring ``cross_app_relations`` must
    materialize a relation field on the source EntryType pointing at the
    target App + track + entry_type.
    """
    from app.models.edges import CONTAINS
    from app.models.nodes import App, EntryType, Track, Workspace
    from app.services.app_graph import get_track_attached_operational_model
    from app.services.operational_model_loader import LibraryProfileSpec
    from app.services.operational_model_workspace_init import (
        init_workspace_from_profile,
    )

    monkeypatch.setattr(
        "app.services.operational_model_loader.load_library_operational_models",
        lambda: [
            LibraryProfileSpec(
                name="X",
                slug="x",
                version="1.0.0",
                description="",
                manifest={
                    "scope": "workspace",
                    "workspace": {
                        "apps": [
                            {
                                "slug": "src",
                                "name": "Src",
                                "profile": {
                                    "scope": "app",
                                    "app": {
                                        "tracks": [
                                            {
                                                "key": "src_t",
                                                "name": "ST",
                                                "provision_on_create": True,
                                                "entry_types": [
                                                    {"key": "src_e", "name": "src_e"}
                                                ],
                                            }
                                        ]
                                    },
                                },
                            },
                            {
                                "slug": "tgt",
                                "name": "Tgt",
                                "profile": {
                                    "scope": "app",
                                    "app": {
                                        "tracks": [
                                            {
                                                "key": "tgt_t",
                                                "name": "TT",
                                                "provision_on_create": True,
                                                "entry_types": [
                                                    {"key": "tgt_e", "name": "tgt_e"}
                                                ],
                                            }
                                        ]
                                    },
                                },
                            },
                        ],
                        "cross_app_relations": [
                            {
                                "key": "src_to_tgt",
                                "source": {
                                    "app": "src",
                                    "track": "src_t",
                                    "entry_type": "src_e",
                                    "field": "ref",
                                },
                                "target": {
                                    "app": "tgt",
                                    "track": "tgt_t",
                                    "entry_type": "tgt_e",
                                },
                            },
                        ],
                    },
                },
                scope="platform",
                library_package=True,
                bundle_dir=Path("/x"),
            )
        ],
    )

    ws = await make_org_workspace(name="cross-test", kind="organization")
    result = await init_workspace_from_profile(ws, "x", actor_id="u1")
    assert len(result.apps_created) == 2
    # Relation was materialized — by key.
    assert (
        "src_to_tgt" in result.relations_created
    ), f"expected 'src_to_tgt' in relations_created, got {result.relations_created}"

    # applied_profiles must record the bundle.
    ws2 = await Workspace.get(ws.id)
    assert any(e.get("slug") == "x" for e in ws2.applied_profiles)

    # The source EntryType's form_schema must now carry a relation field
    # with the cross-App flags set.
    src_app = next(
        a
        for a in [await App.get(aid) for aid in result.apps_created]
        if a.name == "Src"
    )
    tgt_app = next(
        a
        for a in [await App.get(aid) for aid in result.apps_created]
        if a.name == "Tgt"
    )

    src_tracks: list = await src_app.nodes(edge=[CONTAINS], node=["Track"])
    assert len(src_tracks) >= 1
    src_track: Track = src_tracks[0]
    src_tcp = await get_track_attached_operational_model(src_track)
    assert src_tcp is not None
    src_ets: list = await src_tcp.nodes(edge=[CONTAINS], node=["EntryType"])
    # The source EntryType is the one whose name matches the relation's
    # ``source.entry_type`` slug — pick by name (a default "Post"
    # EntryType also exists on every newly-provisioned track).
    src_et: EntryType = next(
        (e for e in src_ets if str(getattr(e, "name", "")).lower() == "src_e"),
        None,
    )
    assert src_et is not None, f"src_e EntryType missing in {[e.name for e in src_ets]}"
    rel_fields = [
        f
        for f in (src_et.form_schema.get("fields") or [])
        if isinstance(f, dict) and f.get("type") == "relation"
    ]
    assert len(rel_fields) == 1, f"expected 1 relation field, got {rel_fields}"
    rf = rel_fields[0]
    assert rf["key"] == "ref"
    assert rf["relation"]["allow_cross_app"] is True
    assert rf["relation"]["target_app"] == tgt_app.id
    assert "tgt_e" in rf["relation"]["target_entry_types"]
    assert "tgt_t" in rf["relation"]["target_track_types"]
