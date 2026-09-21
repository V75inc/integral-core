"""Contract: Asset Register builds to a portable deterministic App artifact."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from nacl.encoding import Base64Encoder
from nacl.signing import SigningKey

from app.agentive.services.execution_runs import RunStep
from app.agentive.tooling.dispatch import dispatch_tool
from app.models.edges import CATALOGS, CONTAINS, IS_MEMBER_OF
from app.models.nodes import App, ContentProfile, Entry
from app.services.app_extension_views import serve_extension_view_asset
from app.services.app_graph import (
    ensure_library_catalog_seeded,
    get_or_create_views_registry_for_content_profile,
    get_track_attached_content_profile,
)
from app.services.app_lifecycle import install_app
from app.services.app_operations.context import OperationContext
from app.services.app_operations.dispatch import invoke_app_operation
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.hooks.registry import get_workspace_tools
from app.services.hooks.tool_dispatch import run_tool
from tests.contract.asset_register_helpers import seed_asset_register_library_cp
from tests.fixtures.workspaces import make_org_workspace

REPO = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = REPO / "examples" / "asset-register" / "build.py"
SDK_ROOT = REPO / "sdk" / "python"


def _build(output_dir: Path, *, signing_key: Path | None = None) -> Path:
    command = [sys.executable, str(BUILD_SCRIPT), "--out-dir", str(output_dir)]
    if signing_key is not None:
        command.extend(["--signing-key", str(signing_key)])
    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    archive = next(output_dir.glob("asset-register-*.tar.gz"))
    return archive


@pytest.mark.contract
def test_asset_register_artifact_is_deterministic_and_loadable(tmp_path):
    first = _build(tmp_path / "first")
    second = _build(tmp_path / "second")

    first_digest = hashlib.sha256(first.read_bytes()).hexdigest()
    assert first_digest == hashlib.sha256(second.read_bytes()).hexdigest()
    assert (
        first.with_suffix(first.suffix + ".sha256").read_text().startswith(first_digest)
    )

    extracted = tmp_path / "extensions"
    extracted.mkdir()
    with tarfile.open(first, "r:gz") as bundle:
        names = bundle.getnames()
        assert "asset-register/profile.yaml" in names
        assert "asset-register/tools/custody.py" in names
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        bundle.extractall(extracted)

    specs, issues = load_library_profiles_with_issues(
        package_paths=[extracted], core_only=False, verify_signatures=False
    )
    assert not issues
    spec = next(spec for spec in specs if spec.slug == "asset-register")
    assert spec.bundle_dir == extracted / "asset-register"


@pytest.mark.contract
def test_asset_register_signed_artifact_verifies_after_extraction(
    tmp_path, monkeypatch
):
    signing_key = SigningKey.generate()
    key_path = tmp_path / "asset-register-private-key.txt"
    key_path.write_text(signing_key.encode(Base64Encoder).decode())
    archive = _build(tmp_path / "signed", signing_key=key_path)
    extracted = tmp_path / "extensions"
    extracted.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(extracted)

    monkeypatch.setenv(
        "INTEGRAL_PROFILE_PUBKEY",
        signing_key.verify_key.encode(Base64Encoder).decode(),
    )
    specs, issues = load_library_profiles_with_issues(
        package_paths=[extracted], core_only=False, verify_signatures=True
    )
    assert not issues
    spec = next(spec for spec in specs if spec.slug == "asset-register")
    assert spec.signature_verified is True
    assert spec.signature_reason == "valid"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_extracted_asset_register_materializes_its_warranty_schedule(
    tmp_path, monkeypatch
):
    archive = _build(tmp_path / "package")
    extensions = tmp_path / "extensions"
    extensions.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(extensions)
    bundle_dir = extensions / "asset-register"

    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(extensions))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    monkeypatch.setattr(
        "app.agentive.services.uplink_registry._scheduler_available", lambda: True
    )
    from app.agentive.nodes import RoutineTask

    workspace = await make_org_workspace("ws-archive-warranty")

    # Do not construct a library ContentProfile directly here. The browser's
    # "Manage apps" dialog reads the graph-backed library catalog, so this
    # exercises the same disk discovery -> catalog materialization boundary an
    # independently extracted App relies on after Core starts.
    catalog_report = await ensure_library_catalog_seeded()
    assert "asset-register" in (
        set(catalog_report["added"]) | set(catalog_report["updated"])
    )
    cataloged = await ContentProfile.find(
        {"context.library_package": True, "context.metadata.slug": "asset-register"}
    )
    assert cataloged
    library_cp = cataloged[0] if isinstance(cataloged, list) else cataloged
    assert library_cp.metadata["bundle_dir_path"] == str(bundle_dir)
    installed = await install_app(
        workspace_id=workspace.id,
        library_cp_id=library_cp.id,
        actor_id="u_archive_warranty",
        include_seed_data=False,
    )
    routines = await RoutineTask.find({"source_app_id": installed["app_id"]})
    assert len(routines) == 1
    assert routines[0].source_schedule_key == "asset_admin:0"
    assert routines[0].status == "active"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_extracted_asset_register_rehydrates_tools_without_duplicate_schedule(
    tmp_path, monkeypatch
):
    """A process restart restores extracted-App tools while retaining its routine."""
    archive = _build(tmp_path / "package")
    extensions = tmp_path / "extensions"
    extensions.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(extensions)
    bundle_dir = extensions / "asset-register"

    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(extensions))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    monkeypatch.setattr(
        "app.agentive.services.uplink_registry._scheduler_available", lambda: True
    )
    from app.agentive.nodes import RoutineTask
    from app.services.hooks.install_hook import rehydrate_all_installed_bundles
    from app.services.hooks.registry import unregister_bundle_registrations

    workspace = await make_org_workspace("ws-archive-rehydrate")
    library_cp = await seed_asset_register_library_cp(bundle_dir=bundle_dir)
    installed = await install_app(
        workspace_id=workspace.id,
        library_cp_id=library_cp.id,
        actor_id="u_archive_rehydrate",
        include_seed_data=False,
    )
    original_routines = await RoutineTask.find({"source_app_id": installed["app_id"]})
    assert len(original_routines) == 1
    routine_id = original_routines[0].id
    assert "review_warranties" in get_workspace_tools(workspace.id)

    # Simulate the in-memory registry loss of a process restart. The durable
    # routine stays in the graph; Core startup must restore the extracted
    # package registrations without creating a second schedule.
    unregister_bundle_registrations(workspace.id, "asset-register")
    assert "review_warranties" not in get_workspace_tools(workspace.id)
    await rehydrate_all_installed_bundles()

    assert "review_warranties" in get_workspace_tools(workspace.id)
    rehydrated_routines = await RoutineTask.find({"source_app_id": installed["app_id"]})
    assert [routine.id for routine in rehydrated_routines] == [routine_id]
    assert rehydrated_routines[0].status == "active"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_extracted_asset_register_materializes_and_serves_extension_view(
    tmp_path, monkeypatch
):
    """An external archive keeps its view binding through real installation."""
    archive = _build(tmp_path / "package")
    extensions = tmp_path / "extensions"
    extensions.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(extensions)
    bundle_dir = extensions / "asset-register"

    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(extensions))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    workspace = await make_org_workspace("ws-archive-extension-view")
    owners = await workspace.nodes(edge=[IS_MEMBER_OF], direction="in", node=["User"])
    owner = owners[0]
    library_cp = await seed_asset_register_library_cp(bundle_dir=bundle_dir)
    installed = await install_app(
        workspace_id=workspace.id,
        library_cp_id=library_cp.id,
        actor_id=owner.id,
        include_seed_data=False,
    )
    app = await App.get(installed["app_id"])
    assert app is not None
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    assets = next(track for track in tracks if track.title == "Assets")
    track_profile = await get_track_attached_content_profile(assets)
    assert track_profile is not None
    registry = await get_or_create_views_registry_for_content_profile(
        track_profile, track=assets
    )
    views = await registry.nodes(edge=[CATALOGS], node=["View"])
    detail_view = next(view for view in views if view.name == "Asset detail")
    assert detail_view.type == "extension_view"
    assert detail_view.config["extension_view_key"] == "asset_detail"

    asset_path, media_type = await serve_extension_view_asset(
        user_id=owner.id,
        workspace_id=workspace.id,
        app_id=app.id,
        view_key="asset_detail",
        asset_path="index.html",
    )
    assert asset_path == bundle_dir / "views" / "asset_detail" / "index.html"
    assert "html" in media_type


@pytest.mark.contract
@pytest.mark.asyncio
async def test_extracted_asset_register_runs_read_operation_through_dispatcher(
    tmp_path, monkeypatch
):
    """A public read operation uses Core's policy and dispatch surface."""
    archive = _build(tmp_path / "package")
    extensions = tmp_path / "extensions"
    extensions.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(extensions)
    bundle_dir = extensions / "asset-register"

    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(extensions))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    # The external App imports its public SDK package, just as it does in the
    # independently installed Core+SDK artifact lane.
    monkeypatch.syspath_prepend(str(SDK_ROOT))
    workspace = await make_org_workspace("ws-archive-dispatch")
    owners = await workspace.nodes(edge=[IS_MEMBER_OF], direction="in", node=["User"])
    owner = owners[0]
    library_cp = await seed_asset_register_library_cp(bundle_dir=bundle_dir)
    installed = await install_app(
        workspace_id=workspace.id,
        library_cp_id=library_cp.id,
        actor_id=owner.id,
        include_seed_data=False,
    )

    result = await invoke_app_operation(
        user_id=owner.id,
        workspace_id=workspace.id,
        app_id=installed["app_id"],
        operation_key="list_available_assets",
        payload={"limit": 10},
    )

    assert result["output"]["ok"] is True
    assert result["output"]["assets"] == []
    assert result["evidence"]["package_slug"] == "asset-register"
    assert result["evidence"]["applied_scope"] == f"ws:{workspace.id}"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_extracted_asset_register_read_operation_over_http(
    tmp_path, monkeypatch, test_user, authenticated_client
):
    """An extracted App executes through the authenticated public endpoint."""
    archive = _build(tmp_path / "package")
    extensions = tmp_path / "extensions"
    extensions.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(extensions)
    bundle_dir = extensions / "asset-register"

    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(extensions))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    monkeypatch.syspath_prepend(str(SDK_ROOT))
    workspace = await make_org_workspace("ws-archive-http-operation")
    await test_user.connect(
        workspace, edge=IS_MEMBER_OF, role="owner", joined_at="2026-01-01T00:00:00Z"
    )
    library_cp = await seed_asset_register_library_cp(bundle_dir=bundle_dir)
    installed = await install_app(
        workspace_id=workspace.id,
        library_cp_id=library_cp.id,
        actor_id=test_user.id,
        include_seed_data=False,
    )

    response = await authenticated_client.post(
        f"/api/extensions/{installed['app_id']}/operations/list_available_assets",
        json={"input": {"limit": 10}},
        headers={"X-Integral-Scope": f"ws:{workspace.id}"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["app_id"] == installed["app_id"]
    assert payload["operation_key"] == "list_available_assets"
    assert payload["output"]["ok"] is True
    assert payload["output"]["assets"] == []
    assert payload["evidence"]["package_slug"] == "asset-register"
    assert payload["evidence"]["applied_scope"] == f"ws:{workspace.id}"
    steps = await RunStep.find({"run_id": payload["receipt"]["run_id"]})
    assert [step.kind for step in steps] == ["query"]


@pytest.mark.contract
@pytest.mark.asyncio
async def test_extracted_asset_register_read_operation_over_mcp_dispatch(
    tmp_path, monkeypatch, test_user
):
    """The public MCP operation tool reaches an extracted App under its scope."""
    archive = _build(tmp_path / "package")
    extensions = tmp_path / "extensions"
    extensions.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(extensions)
    bundle_dir = extensions / "asset-register"

    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(extensions))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    monkeypatch.syspath_prepend(str(SDK_ROOT))
    workspace = await make_org_workspace("ws-archive-mcp-operation")
    await test_user.connect(
        workspace, edge=IS_MEMBER_OF, role="owner", joined_at="2026-01-01T00:00:00Z"
    )
    library_cp = await seed_asset_register_library_cp(bundle_dir=bundle_dir)
    installed = await install_app(
        workspace_id=workspace.id,
        library_cp_id=library_cp.id,
        actor_id=test_user.id,
        include_seed_data=False,
    )

    result = await dispatch_tool(
        "integral_invoke_app_operation",
        {
            "app_id": installed["app_id"],
            "operation_key": "list_available_assets",
            "input": {"limit": 10},
        },
        principal_id=test_user.id,
        scope=workspace.id,
    )

    assert result.is_error is False
    assert result.data["output"]["ok"] is True
    assert result.data["output"]["assets"] == []
    assert result.data["evidence"]["package_slug"] == "asset-register"
    assert result.data["evidence"]["applied_scope"] == f"ws:{workspace.id}"


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_extracted_asset_register_mutation_replays_one_receipt(
    tmp_path, monkeypatch, postgres_raw_db
):
    """A package mutation commits once and replays its durable receipt."""
    del postgres_raw_db  # Fixture establishes the live Postgres graph context.
    archive = _build(tmp_path / "package")
    extensions = tmp_path / "extensions"
    extensions.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(extensions)
    bundle_dir = extensions / "asset-register"

    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(extensions))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    monkeypatch.syspath_prepend(str(SDK_ROOT))
    workspace = await make_org_workspace("ws-archive-mutation")
    owners = await workspace.nodes(edge=[IS_MEMBER_OF], direction="in", node=["User"])
    owner = owners[0]
    library_cp = await seed_asset_register_library_cp(bundle_dir=bundle_dir)
    installed = await install_app(
        workspace_id=workspace.id,
        library_cp_id=library_cp.id,
        actor_id=owner.id,
        include_seed_data=False,
    )
    arguments = {"asset_tag": "ARCHIVE-IDEM-001", "title": "Archive Idem Laptop"}

    first = await invoke_app_operation(
        user_id=owner.id,
        workspace_id=workspace.id,
        app_id=installed["app_id"],
        operation_key="register_asset",
        payload=arguments,
        idempotency_key="archive-register-001",
    )
    replay = await invoke_app_operation(
        user_id=owner.id,
        workspace_id=workspace.id,
        app_id=installed["app_id"],
        operation_key="register_asset",
        payload=arguments,
        idempotency_key="archive-register-001",
    )

    asset_id = first["output"]["asset"]["entry_id"]
    assert (await Entry.get(asset_id)) is not None
    assert replay["output"] == first["output"]
    assert first["operation_receipt"]["replayed"] is False
    assert replay["operation_receipt"]["replayed"] is True


@pytest.mark.contract
@pytest.mark.asyncio
async def test_extracted_asset_register_executes_registered_custody_tools(
    tmp_path, monkeypatch
):
    archive = _build(tmp_path / "package")
    extensions = tmp_path / "extensions"
    extensions.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(extensions)
    bundle_dir = extensions / "asset-register"
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(extensions))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    monkeypatch.syspath_prepend(str(SDK_ROOT))

    workspace = await make_org_workspace("ws-archive-custody")
    owners = await workspace.nodes(edge=[IS_MEMBER_OF], direction="in", node=["User"])
    owner = owners[0]
    library_cp = await seed_asset_register_library_cp(bundle_dir=bundle_dir)
    installed = await install_app(
        workspace_id=workspace.id,
        library_cp_id=library_cp.id,
        actor_id=owner.id,
        include_seed_data=False,
    )
    app = await App.get(installed["app_id"])
    assert app is not None
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    custodians = next(track for track in tracks if track.title == "Custodians")
    ctx = OperationContext(
        user_id=owner.id,
        workspace_id=workspace.id,
        scope=f"operation:{app.id}:register_asset",
        app_id=app.id,
        operation_key="register_asset",
    )
    custodian = await ctx.create_entry(
        track_id=custodians.id,
        entry_type_key="custodian",
        title="Archive Custodian",
        custom_fields={"contact_email": "archive-custodian@example.test"},
    )
    assert custodian is not None

    tools = get_workspace_tools(workspace.id)
    registered = await run_tool(
        tools["register_asset"],
        {"asset_tag": "ARCHIVE-001", "title": "Archive Laptop"},
        ctx,
    )
    assert registered["ok"] is True
    asset_id = registered["asset"]["entry_id"]

    ctx.operation_key = "check_out_asset"
    checked_out = await run_tool(
        tools["check_out_asset"],
        {"asset_id": asset_id, "custodian_id": custodian.id},
        ctx,
    )
    assert checked_out["ok"] is True
    assert checked_out["asset_id"] == asset_id
