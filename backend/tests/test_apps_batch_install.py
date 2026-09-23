"""Phase 32 — batch install regression suite.

Covers:
- Topological ordering (intra-batch dep installs before dependent).
- Idempotency (re-running skips already-installed bundles).
- Override pass-through (name + description override the manifest defaults).
- Default fallback (omitted name / description fill from package.name /
  package.description).
- Partial-success protocol (one bundle's failure does not halt the rest).
- Circular dep rejection (cycle inside batch raises before any install).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.models.edges import IS_MEMBER_OF
from app.models.nodes import App, OperationalModel, Workspace
from app.services.app_batch_install import _has_path, _topo_sort, batch_install
from app.utils.time import utc_now_iso

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_workspace(name: str = "Batch WS") -> Workspace:
    now = utc_now_iso()
    return await Workspace.create(
        kind="organization",
        workspace_type="company",
        name=name,
        name_fold=name.casefold(),
        created_at=now,
        updated_at=now,
    )


def _manifest(
    *,
    name: str,
    slug: Optional[str] = None,
    requires: Optional[List[str]] = None,
    requires_specs: Optional[List[Dict[str, Any]]] = None,
    description: str = "",
) -> Dict[str, Any]:
    pkg_desc = description or f"Bundle {name} description"
    pkg_slug = slug or name.lower()
    if requires_specs is not None:
        requires_block = requires_specs
    else:
        requires_block = [
            {"key": r, "min_version": "0.0.0", "optional": False}
            for r in (requires or [])
        ]
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {
            "name": name,
            "slug": pkg_slug,
            "key": pkg_slug,
            "version": "1.0.0",
            "description": pkg_desc,
            "tags": ["test"],
        },
        "app": {
            "description": pkg_desc,
            "requires_apps": requires_block,
            "tracks": [
                {
                    "key": f"{pkg_slug}_track",
                    "name": f"{name} Track",
                    "entry_types": [
                        {"key": f"{pkg_slug}_entry", "name": f"{name} Entry"}
                    ],
                }
            ],
        },
    }


async def _seed_library(
    name: str,
    slug: Optional[str] = None,
    requires: Optional[List[str]] = None,
    requires_specs: Optional[List[Dict[str, Any]]] = None,
) -> OperationalModel:
    manifest = _manifest(
        name=name, slug=slug, requires=requires, requires_specs=requires_specs
    )
    return await OperationalModel.create(
        name=name,
        slug=slug or name.lower(),
        manifest=manifest,
        library_package=True,
        scope="app",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )


# ---------------------------------------------------------------------------
# _topo_sort & _has_path (pure function — no DB)
# ---------------------------------------------------------------------------


def test_has_path():
    edges = {"a": {"b"}, "b": {"c"}, "c": set()}
    assert _has_path("a", "c", edges) is True
    assert _has_path("c", "a", edges) is False
    assert _has_path("a", "a", edges) is True


def test_topo_sort_linear_chain():
    order, cycle = _topo_sort(
        nodes=["a", "b", "c"],
        edges={"a": ["b"], "b": ["c"], "c": []},
    )
    assert order == ["c", "b", "a"]
    assert cycle == []


def test_topo_sort_parallel_independent():
    order, cycle = _topo_sort(
        nodes=["a", "b", "c"],
        edges={"a": [], "b": [], "c": []},
    )
    assert sorted(order) == ["a", "b", "c"]
    assert cycle == []


def test_topo_sort_detects_cycle():
    order, cycle = _topo_sort(
        nodes=["a", "b", "c"],
        edges={"a": ["b"], "b": ["c"], "c": ["a"]},
    )
    assert order == []
    assert cycle == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# batch_install end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_install_topological_order(test_user) -> None:
    ws = await _make_workspace("BatchTopo")
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso())

    # Dep graph: dependent_alpha → base_zulu (so zulu installs first)
    base = await _seed_library("base_zulu")
    dep = await _seed_library("dependent_alpha", requires=["base_zulu"])

    result = await batch_install(
        workspace_id=ws.id,
        items=[
            {"library_cp_id": dep.id},
            {"library_cp_id": base.id},
        ],
        actor_id=test_user.id,
    )

    assert len(result["installed"]) == 2
    assert result["failed"] == []
    assert result["skipped"] == []
    # base_zulu must come before dependent_alpha in the install order.
    order = result["order"]
    assert order.index(base.id) < order.index(dep.id)


@pytest.mark.asyncio
async def test_batch_install_idempotency_skips_existing(test_user) -> None:
    ws = await _make_workspace("BatchIdem")
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso())
    bundle = await _seed_library("idem_bundle")

    first = await batch_install(
        workspace_id=ws.id,
        items=[{"library_cp_id": bundle.id}],
        actor_id=test_user.id,
    )
    assert len(first["installed"]) == 1

    second = await batch_install(
        workspace_id=ws.id,
        items=[{"library_cp_id": bundle.id}],
        actor_id=test_user.id,
    )
    assert len(second["installed"]) == 0
    assert len(second["skipped"]) == 1
    assert second["skipped"][0]["reason"] == "already_installed"


@pytest.mark.asyncio
async def test_batch_install_default_label_fallback(test_user) -> None:
    """When name/description omitted, App.name + .description fall back to package fields."""
    ws = await _make_workspace("BatchDefaults")
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso())
    bundle = await _seed_library("default_label_bundle")

    result = await batch_install(
        workspace_id=ws.id,
        items=[{"library_cp_id": bundle.id}],
        actor_id=test_user.id,
    )
    assert len(result["installed"]) == 1
    app = await App.get(result["installed"][0]["app_id"])
    assert app is not None
    assert app.name == "default_label_bundle"
    assert "default_label_bundle" in (app.description or "")


@pytest.mark.asyncio
async def test_batch_install_override_pass_through(test_user) -> None:
    ws = await _make_workspace("BatchOverride")
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso())
    bundle = await _seed_library("override_target_bundle")

    result = await batch_install(
        workspace_id=ws.id,
        items=[
            {
                "library_cp_id": bundle.id,
                "name": "Branded Override Name",
                "description": "Branded override description",
            }
        ],
        actor_id=test_user.id,
    )
    assert len(result["installed"]) == 1
    app = await App.get(result["installed"][0]["app_id"])
    assert app is not None
    assert app.name == "Branded Override Name"
    assert app.description == "Branded override description"


@pytest.mark.asyncio
async def test_batch_install_partial_success(test_user) -> None:
    """One bundle's install failure does not halt the rest of the batch."""
    ws = await _make_workspace("BatchPartial")
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso())
    ok = await _seed_library("partial_ok")
    # broken: declares a hard dep on a bundle that's neither in the batch nor installed.
    broken_manifest = _manifest(name="partial_broken", requires=["never_installed_dep"])
    broken = await OperationalModel.create(
        name="partial_broken",
        manifest=broken_manifest,
        library_package=True,
        scope="app",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )

    result = await batch_install(
        workspace_id=ws.id,
        items=[{"library_cp_id": ok.id}, {"library_cp_id": broken.id}],
        actor_id=test_user.id,
    )
    assert len(result["installed"]) == 1
    assert len(result["failed"]) == 1
    assert result["failed"][0]["library_cp_id"] == broken.id


@pytest.mark.asyncio
async def test_batch_install_circular_dep_rejected(test_user) -> None:
    ws = await _make_workspace("BatchCircular")
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso())
    a = await _seed_library("circ_a", requires=["circ_b"])
    b = await _seed_library("circ_b", requires=["circ_a"])

    with pytest.raises(Exception) as exc_info:
        await batch_install(
            workspace_id=ws.id,
            items=[{"library_cp_id": a.id}, {"library_cp_id": b.id}],
            actor_id=test_user.id,
        )
    # BadRequestError carries error_code="bad_request"; just confirm the cycle wording surfaces.
    assert "circular" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_batch_install_empty_items_rejected(test_user) -> None:
    ws = await _make_workspace("BatchEmpty")
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso())
    with pytest.raises(Exception):
        await batch_install(workspace_id=ws.id, items=[], actor_id=test_user.id)


@pytest.mark.asyncio
async def test_batch_install_duplicate_library_in_batch_rejected(test_user) -> None:
    ws = await _make_workspace("BatchDupe")
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso())
    bundle = await _seed_library("dupe_bundle")
    with pytest.raises(Exception) as exc_info:
        await batch_install(
            workspace_id=ws.id,
            items=[{"library_cp_id": bundle.id}, {"library_cp_id": bundle.id}],
            actor_id=test_user.id,
        )
    assert "duplicate" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_batch_install_mutual_soft_dependencies_allowed(test_user) -> None:
    """Mutual soft dependencies (like CRM <-> Projects) do not cause a circular dep error."""
    ws = await _make_workspace("BatchMutualSoft")
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso())

    crm = await _seed_library(
        "crm",
        requires_specs=[{"key": "projects", "optional": True, "min_version": "1.0.0"}],
    )
    projects = await _seed_library(
        "projects",
        requires_specs=[{"key": "crm", "optional": True, "min_version": "1.0.0"}],
    )

    result = await batch_install(
        workspace_id=ws.id,
        items=[{"library_cp_id": crm.id}, {"library_cp_id": projects.id}],
        actor_id=test_user.id,
    )
    assert len(result["installed"]) == 2
    assert result["failed"] == []


@pytest.mark.asyncio
async def test_batch_install_hard_and_soft_cycle_resolved(test_user) -> None:
    """When A hard-requires B, and B soft-requires A, B installs first without error."""
    ws = await _make_workspace("BatchHardSoft")
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso())

    # A hard-requires B
    bundle_a = await _seed_library(
        "hard_a",
        requires_specs=[{"key": "soft_b", "optional": False, "min_version": "1.0.0"}],
    )
    # B soft-requires A
    bundle_b = await _seed_library(
        "soft_b",
        requires_specs=[{"key": "hard_a", "optional": True, "min_version": "1.0.0"}],
    )

    result = await batch_install(
        workspace_id=ws.id,
        items=[{"library_cp_id": bundle_a.id}, {"library_cp_id": bundle_b.id}],
        actor_id=test_user.id,
    )
    assert len(result["installed"]) == 2
    assert result["failed"] == []
    # bundle_b must install before bundle_a because bundle_a has a hard requirement on bundle_b
    order = result["order"]
    assert order.index(bundle_b.id) < order.index(bundle_a.id)


@pytest.mark.asyncio
async def test_batch_install_workspace_provisioning_crm_and_projects(test_user) -> None:
    """Simulate workspace creation provisioning with CRM and Projects apps.

    CRM has display name 'CRM', slug 'crm', requires 'projects' (optional: True).
    Projects has display name 'Projects', slug 'projects', requires 'crm' (optional: True).
    Uses resolve_dependencies=True and use_default_settings=True (POST /api/workspaces contract).
    """
    ws = await _make_workspace("BatchWorkspaceProvisioning")
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso())

    crm = await _seed_library(
        name="CRM",
        slug="crm",
        requires_specs=[
            {
                "key": "projects",
                "optional": True,
                "min_version": "1.0.0",
                "reason": "Won-opportunity to project handoff lights up when Projects is installed",
            }
        ],
    )
    projects = await _seed_library(
        name="Projects",
        slug="projects",
        requires_specs=[
            {
                "key": "crm",
                "optional": True,
                "min_version": "1.0.0",
            },
            {
                "key": "sales",
                "optional": True,
                "min_version": "1.0.0",
            },
        ],
    )

    result = await batch_install(
        workspace_id=ws.id,
        items=[{"library_cp_id": projects.id}, {"library_cp_id": crm.id}],
        actor_id=test_user.id,
        resolve_dependencies=True,
        use_default_settings=True,
    )
    assert len(result["installed"]) == 2
    assert result["failed"] == []
    assert result["skipped"] == []
    installed_names = {r["name"] for r in result["installed"]}
    assert "Projects" in installed_names
    assert "CRM" in installed_names
