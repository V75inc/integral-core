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

from app.models.edges import CONTAINS, IS_MEMBER_OF
from app.models.nodes import App
from app.services.app_lifecycle import install_app
from app.services.app_operations.context import OperationContext
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
    library_cp = await seed_asset_register_library_cp(bundle_dir=bundle_dir)
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
