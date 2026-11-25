"""Packaged App portable export/import (archive version 2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.api.errors import BadRequestError
from app.models.edges import CONTAINS, IS_MEMBER_OF
from app.models.nodes import (
    OPERATIONAL_MODELS_REGISTRY_ID,
    App,
    OperationalModels,
    Workspace,
)
from app.schemas.portable_transfer import ExportSelection, PackagePayload
from app.services.app_graph import (
    catalog_workspace,
    ensure_catalog_edge,
    get_app_attached_operational_model,
)
from app.services.app_lifecycle import install_app
from app.services.operational_model_library_seed import upsert_seeded_library_packages
from app.services.operational_model_loader import (
    compute_bundle_fingerprint,
    load_library_operational_models,
)
from app.services.permissions import get_user_node
from app.services.personal_workspace import ensure_personal_workspace_for_user_id
from app.services.portable_archive import decode_archive, encode_archive
from app.services.portable_package import (
    fingerprint_members,
    member_prefix_for_slug,
    pack_bundle_dir,
    validate_package_members,
)
from app.services.portable_transfer import (
    export_resources,
    import_resources,
    manifest_has_runtime,
    read_archive,
)
from app.utils.time import utc_now_iso


def _write_demo_bundle(
    root: Path,
    slug: str = "portable-demo",
    *,
    protected: bool = False,
    with_settings: bool = False,
) -> Path:
    bundle = root / slug
    bundle.mkdir(parents=True)
    (bundle / "tools").mkdir()
    (bundle / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (bundle / "tools" / "hello.py").write_text(
        "def echo(message: str = '') -> dict:\n    return {'message': message}\n",
        encoding="utf-8",
    )
    entry_type = {
        "key": "stock_position" if protected else "note",
        "name": "Stock Position" if protected else "Note",
        "fields": [
            {"key": "body", "name": "Body", "type": "text"},
        ],
    }
    if protected:
        entry_type["fields"].append(
            {"key": "qty_on_hand", "name": "Qty on hand", "type": "number"}
        )
    tool_key = "echo" if slug == "portable-demo" else f"{slug}.echo"
    app_body: dict = {
        "description": "Packaged portable fixture",
        "tracks": [
            {
                "key": "notes",
                "name": "Notes",
                "description": "Notes track",
                "entry_types": [entry_type],
            }
        ],
        "tools": [
            {
                "key": tool_key,
                "name": "Echo",
                "description": "Echo tool",
                "handler_ref": "tools.hello:echo",
                "parameters_schema": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                },
            }
        ],
    }
    if protected:
        app_body["protected_state"] = {
            "stock_position": {
                "fields": ["qty_on_hand"],
                "operations": [tool_key],
            }
        }
    if with_settings:
        app_body["settings_schema"] = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "allow_negative_stock": {
                    "type": "boolean",
                    "title": "Allow negative stock",
                    "default": False,
                },
                "default_currency": {
                    "type": "string",
                    "title": "Default currency",
                },
            },
        }
    manifest = {
        "integral_operational_model_version": 3,
        "scope": "app",
        "package": {
            "name": "Portable Demo",
            "slug": slug,
            "class": "community_app",
            "version": "1.0.0",
            "trust_tier": "trusted",
            "description": "Portable transfer fixture",
        },
        "app": app_body,
    }
    (bundle / "operational-model.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    return bundle


async def _admit_bundle(bundle: Path):
    specs = load_library_operational_models(
        package_paths=[bundle.parent], verify_signatures=True, core_only=False
    )
    matched = [spec for spec in specs if spec.slug == bundle.name]
    assert matched, "demo bundle failed to load"
    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    await upsert_seeded_library_packages(
        catalog_registry=registry,
        specs=matched,
        now_iso=utc_now_iso(),
        ensure_catalog_edge=ensure_catalog_edge,
    )
    from app.models.nodes import OperationalModel

    rows = await OperationalModel.find({"context.metadata.slug": bundle.name})
    library = next(row for row in rows if getattr(row, "library_package", False))
    return library


def test_pack_bundle_fingerprint_stable(tmp_path: Path):
    bundle = _write_demo_bundle(tmp_path)
    packed, fingerprint = pack_bundle_dir(bundle, bundle.name)
    assert f"package/{bundle.name}/operational-model.yaml" in packed
    assert f"package/{bundle.name}/tools/hello.py" in packed
    assert fingerprint == fingerprint_members(
        packed, member_prefix_for_slug(bundle.name)
    )
    assert fingerprint == compute_bundle_fingerprint(bundle)


def test_validate_package_members_rejects_tampered_fingerprint(tmp_path: Path):
    bundle = _write_demo_bundle(tmp_path)
    packed, fingerprint = pack_bundle_dir(bundle, bundle.name)
    payload = PackagePayload(
        slug=bundle.name,
        version="1.0.0",
        fingerprint=fingerprint,
        member_prefix=member_prefix_for_slug(bundle.name),
    )
    validate_package_members(payload, packed)
    packed[f"package/{bundle.name}/tools/hello.py"] = b"def echo():\n    return {}\n"
    with pytest.raises(BadRequestError, match="fingerprint"):
        validate_package_members(payload, packed)


@pytest.mark.asyncio
async def test_runtime_without_package_payload_still_refused():
    descriptor = {
        "kind": "app",
        "mode": "configuration",
        "version": 1,
        "apps": [
            {
                "ref": "app",
                "name": "Unsafe",
                "manifest": {"app": {"tools": [{"key": "x", "handler_ref": "a:b"}]}},
            }
        ],
    }
    with pytest.raises(BadRequestError, match="runtime"):
        await read_archive(encode_archive(descriptor, {}))


@pytest.mark.asyncio
async def test_packaged_app_export_import_round_trip(tmp_path, test_user, monkeypatch):
    from app.services.attachment_metadata.service import get_metadata_extraction_service
    from app.services.entry_create import create_entry_in_track

    packages = tmp_path / "packages"
    packages.mkdir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(packages))

    monkeypatch.setattr(
        get_metadata_extraction_service(), "schedule_extraction", lambda *_: None
    )

    bundle = _write_demo_bundle(packages)
    # Preserve canonical signature coverage. Immutable signed package hosts run
    # with bytecode writes disabled, including in the cold-start interpreter.
    import sys

    from nacl.encoding import Base64Encoder
    from nacl.signing import SigningKey

    from app.services.operational_model_signature import compute_bundle_payload

    key = SigningKey.generate()
    monkeypatch.setenv(
        "INTEGRAL_OPERATIONAL_MODEL_PUBKEY",
        key.verify_key.encode(encoder=Base64Encoder).decode(),
    )
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    (bundle / "signature.bin").write_bytes(
        key.sign(compute_bundle_payload(bundle)).signature
    )
    library = await _admit_bundle(bundle)
    source_ws = await ensure_personal_workspace_for_user_id(test_user.id)
    installed = await install_app(
        workspace_id=source_ws.id,
        library_cp_id=library.id,
        actor_id=test_user.id,
        include_seed_data=False,
        visibility="private",
    )
    assert installed["status"] == "active"
    app = await App.get(installed["app_id"])
    assert app is not None
    attached = await get_app_attached_operational_model(app)
    assert manifest_has_runtime(attached.manifest if attached else {})
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    assert tracks
    track = tracks[0]
    from app.services.app_graph import get_track_attached_operational_model

    cp = await get_track_attached_operational_model(track)
    types = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    entry = await create_entry_in_track(
        track=track,
        user_id=test_user.id,
        workspace_id=source_ws.id,
        title="Hello note",
        type_id=types[0].id,
        custom_fields={"body": "portable"},
    )

    from app.models.edges import HAS_VIEW
    from app.models.nodes import Tag

    tag = await Tag.create(
        name="Important",
        name_fold="important",
        app_id=app.id,
        color="#123456",
        aliases=["priority"],
    )
    await attached.connect(tag, edge=CONTAINS)
    entry.tags = [tag.id]
    await entry.save()
    track.title = "Renamed Notes"
    track.title_fold = "renamed notes"
    track.purpose = "Preserved purpose"
    track.accent_color = "#234567"
    track.nav_visible = False
    await track.save()
    source_view = (await track.nodes(edge=[HAS_VIEW], node=["View"]))[0]
    source_view.hidden = True
    source_view.is_default = False
    await source_view.save()

    data = await export_resources(
        user_id=test_user.id,
        workspace_id=source_ws.id,
        selection=ExportSelection(kind="app", ids=[app.id], mode="full"),
    )
    raw, files = decode_archive(data)
    assert raw["version"] == 2
    assert raw["package"]["slug"] == "portable-demo"
    assert any(name.startswith("package/portable-demo/") for name in files)

    dest = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Portable Dest",
        name_fold="portable dest",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    await catalog_workspace(dest)
    profile = await get_user_node(test_user.id)
    if profile is not None:
        await profile.connect(dest, edge=IS_MEMBER_OF, role="owner")
    result = await import_resources(
        user_id=test_user.id, workspace_id=dest.id, data=data
    )
    assert result["apps"] == 1
    assert result["package"]["slug"] == "portable-demo"
    assert result["entries"] == 1
    imported = await App.get(result["resource_ids"][0])
    assert imported is not None
    assert imported.lifecycle_state == "active"
    assert imported.workspace_id == dest.id
    imported_attached = await get_app_attached_operational_model(imported)
    assert manifest_has_runtime(imported_attached.manifest if imported_attached else {})
    imported_tracks = await imported.nodes(edge=[CONTAINS], node=["Track"])
    imported_entries = await imported_tracks[0].nodes(edge=[CONTAINS], node=["Entry"])
    assert any(row.title == entry.title for row in imported_entries)
    assert imported_tracks[0].title == "Renamed Notes"
    assert imported_tracks[0].purpose == "Preserved purpose"
    assert imported_tracks[0].accent_color == "#234567"
    assert imported_tracks[0].nav_visible is False
    copied_view = next(
        row
        for row in await imported_tracks[0].nodes(edge=[HAS_VIEW], node=["View"])
        if row.name == source_view.name
    )
    assert copied_view.hidden is True
    assert copied_view.is_default is False
    copied_tag = await Tag.get(imported_entries[0].tags[0])
    assert copied_tag.app_id == imported.id
    assert copied_tag.name == "Important"
    assert copied_tag.color == "#123456"
    assert copied_tag.aliases == ["priority"]

    # A repeat import must leave the existing destination and runtime intact.
    before = await imported.export(flat=True)
    with pytest.raises(BadRequestError, match="fresh App destination"):
        await import_resources(user_id=test_user.id, workspace_id=dest.id, data=data)
    assert await (await App.get(imported.id)).export(flat=True) == before
    assert len(await imported_tracks[0].nodes(edge=[CONTAINS], node=["Entry"])) == 1

    # Supported registry rehydration in a fresh interpreter, using only durable
    # graph state and configured operator package paths.
    import asyncio
    import json
    import os
    import subprocess
    import sys

    script = """
import asyncio, json, os
from app.models.nodes import App
from app.services.hooks.install_hook import rehydrate_all_installed_bundles
from app.services.hooks.registry import get_workspace_tools
from app.services.app_lifecycle import _resolve_app_bundle_dir
from app.services.operational_model_signature import verify_bundle_signature
from pathlib import Path
async def main():
    await rehydrate_all_installed_bundles()
    app = await App.get(os.environ['PORTABLE_QA_APP'])
    assert verify_bundle_signature(Path(await _resolve_app_bundle_dir(app)), os.environ['INTEGRAL_OPERATIONAL_MODEL_PUBKEY']).reason == 'valid'
    print(json.dumps({'active': app.lifecycle_state, 'fingerprint': app.installed_artifact_fingerprint, 'tool': 'echo' in get_workspace_tools(app.workspace_id), 'bundle': await _resolve_app_bundle_dir(app)}))
asyncio.run(main())
"""
    child = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script],
        env={**os.environ, "PORTABLE_QA_APP": imported.id},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 0, child.stderr
    recovered = json.loads(child.stdout.strip().splitlines()[-1])
    assert recovered == {
        "active": "active",
        "fingerprint": compute_bundle_fingerprint(bundle),
        "tool": True,
        "bundle": str(bundle.resolve()),
    }

    # An ordinary creator failure compensates only the new attempt's graph and
    # runtime. The admitted package and the earlier successful import survive.
    from unittest.mock import AsyncMock

    from app.services.hooks.registry import get_workspace_tools

    failure_dest = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Failure Dest",
        name_fold="failure dest",
    )
    await catalog_workspace(failure_dest)
    await profile.connect(failure_dest, edge=IS_MEMBER_OF, role="owner")
    monkeypatch.setattr(
        "app.services.entry_create.create_entry_in_track",
        AsyncMock(side_effect=RuntimeError("simulated creator failure")),
    )
    with pytest.raises(RuntimeError, match="simulated creator failure"):
        await import_resources(
            user_id=test_user.id, workspace_id=failure_dest.id, data=data
        )
    assert not await App.find({"context.workspace_id": failure_dest.id})
    assert "echo" not in get_workspace_tools(failure_dest.id)
    assert "echo" in get_workspace_tools(dest.id)
    assert (
        compute_bundle_fingerprint(bundle) == before["installed_artifact_fingerprint"]
    )


@pytest.mark.asyncio
async def test_multi_packaged_app_export_import(tmp_path, test_user, monkeypatch):
    from app.services.attachment_metadata.service import get_metadata_extraction_service
    from app.services.entry_create import create_entry_in_track

    packages = tmp_path / "packages"
    packages.mkdir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(packages))
    monkeypatch.setattr(
        get_metadata_extraction_service(), "schedule_extraction", lambda *_: None
    )

    bundle_a = _write_demo_bundle(packages, slug="portable-demo-a")
    bundle_b = _write_demo_bundle(packages, slug="portable-demo-b")
    lib_a = await _admit_bundle(bundle_a)
    lib_b = await _admit_bundle(bundle_b)
    source_ws = await ensure_personal_workspace_for_user_id(test_user.id)
    first = await install_app(
        workspace_id=source_ws.id,
        library_cp_id=lib_a.id,
        actor_id=test_user.id,
        include_seed_data=False,
        visibility="private",
        name_override="Demo A",
    )
    second = await install_app(
        workspace_id=source_ws.id,
        library_cp_id=lib_b.id,
        actor_id=test_user.id,
        include_seed_data=False,
        visibility="private",
        name_override="Demo B",
    )
    app_a = await App.get(first["app_id"])
    app_b = await App.get(second["app_id"])
    track_a = (await app_a.nodes(edge=[CONTAINS], node=["Track"]))[0]
    from app.services.app_graph import get_track_attached_operational_model

    cp_a = await get_track_attached_operational_model(track_a)
    types_a = await cp_a.nodes(edge=[CONTAINS], node=["EntryType"])
    await create_entry_in_track(
        track=track_a,
        user_id=test_user.id,
        workspace_id=source_ws.id,
        title="Note A",
        type_id=types_a[0].id,
        custom_fields={"body": "a"},
    )

    data = await export_resources(
        user_id=test_user.id,
        workspace_id=source_ws.id,
        selection=ExportSelection(
            kind="app",
            ids=[app_a.id, app_b.id],
            mode="full",
        ),
    )
    raw, files = decode_archive(data)
    assert raw["version"] == 2
    assert {pkg["slug"] for pkg in raw["packages"]} == {
        "portable-demo-a",
        "portable-demo-b",
    }
    assert any(name.startswith("package/portable-demo-a/") for name in files)
    assert any(name.startswith("package/portable-demo-b/") for name in files)
    assert {app["package_slug"] for app in raw["apps"]} == {
        "portable-demo-a",
        "portable-demo-b",
    }

    dest = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Multi Dest",
        name_fold="multi dest",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    await catalog_workspace(dest)
    profile = await get_user_node(test_user.id)
    if profile is not None:
        await profile.connect(dest, edge=IS_MEMBER_OF, role="owner")
    result = await import_resources(
        user_id=test_user.id, workspace_id=dest.id, data=data
    )
    assert result["apps"] == 2
    assert len(result["packages"]) == 2
    assert result["entries"] == 1
    imported = [await App.get(rid) for rid in result["resource_ids"]]
    assert all(app and app.lifecycle_state == "active" for app in imported)
    assert {app.name for app in imported} == {"Demo A", "Demo B"}


@pytest.mark.asyncio
@pytest.mark.parametrize("export_mode", ["configuration", "full"])
async def test_mixed_packaged_and_declarative_export_import(
    tmp_path, test_user, monkeypatch, export_mode
):
    from app.services.app_service import create_app_for_user
    from app.services.attachment_metadata.service import get_metadata_extraction_service

    packages = tmp_path / "packages"
    packages.mkdir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(packages))
    monkeypatch.setattr(
        get_metadata_extraction_service(), "schedule_extraction", lambda *_: None
    )

    bundle = _write_demo_bundle(packages, slug="portable-mixed-pkg")
    library = await _admit_bundle(bundle)
    source_ws = await ensure_personal_workspace_for_user_id(test_user.id)
    packaged = await install_app(
        workspace_id=source_ws.id,
        library_cp_id=library.id,
        actor_id=test_user.id,
        include_seed_data=False,
        visibility="private",
        name_override="Packaged Side",
    )
    declarative = await create_app_for_user(
        test_user.id,
        "Declarative Side",
        description="Blank app",
        workspace_id=source_ws.id,
        visibility="private",
    )
    from app.services.app_graph import get_track_attached_operational_model
    from app.services.entry_create import create_entry_in_track

    src = await App.get(packaged["app_id"])
    track = (await src.nodes(edge=[CONTAINS], node=["Track"]))[0]
    model = await get_track_attached_operational_model(track)
    entry_type = (await model.nodes(edge=[CONTAINS], node=["EntryType"]))[0]
    await create_entry_in_track(
        track=track,
        user_id=test_user.id,
        title="Source data",
        type_id=entry_type.id,
        custom_fields={"body": "Only restored on request"},
    )
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=source_ws.id,
        selection=ExportSelection(
            kind="app",
            ids=[packaged["app_id"], declarative.id],
            mode=export_mode,
        ),
    )
    raw, _files = decode_archive(data)
    assert raw["version"] == 2
    assert len(raw["packages"]) == 1
    by_name = {app["name"]: app for app in raw["apps"]}
    assert by_name["Packaged Side"]["package_slug"] == "portable-mixed-pkg"
    assert by_name["Declarative Side"]["package_slug"] == ""
    assert len(by_name["Packaged Side"]["tracks"][0]["entries"]) == (
        1 if export_mode == "full" else 0
    )

    dest = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Mixed Dest",
        name_fold="mixed dest",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    await catalog_workspace(dest)
    profile = await get_user_node(test_user.id)
    if profile is not None:
        await profile.connect(dest, edge=IS_MEMBER_OF, role="owner")
    result = await import_resources(
        user_id=test_user.id, workspace_id=dest.id, data=data, include_data=False
    )
    assert result["entries"] == 0
    assert result["apps"] == 2
    imported = [await App.get(rid) for rid in result["resource_ids"]]
    assert {app.name for app in imported} == {"Packaged Side", "Declarative Side"}
    packaged_app = next(app for app in imported if app.name == "Packaged Side")
    assert packaged_app.lifecycle_state == "active"
    assert manifest_has_runtime(
        (await get_app_attached_operational_model(packaged_app)).manifest
    )


@pytest.mark.asyncio
async def test_legacy_singular_package_archive_still_imports(
    tmp_path, test_user, monkeypatch
):
    from app.services.attachment_metadata.service import get_metadata_extraction_service

    packages = tmp_path / "packages"
    packages.mkdir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(packages))
    monkeypatch.setattr(
        get_metadata_extraction_service(), "schedule_extraction", lambda *_: None
    )

    bundle = _write_demo_bundle(packages, slug="portable-legacy")
    library = await _admit_bundle(bundle)
    source_ws = await ensure_personal_workspace_for_user_id(test_user.id)
    installed = await install_app(
        workspace_id=source_ws.id,
        library_cp_id=library.id,
        actor_id=test_user.id,
        include_seed_data=False,
        visibility="private",
    )
    app = await App.get(installed["app_id"])
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=source_ws.id,
        selection=ExportSelection(kind="app", ids=[app.id], mode="configuration"),
    )
    raw, files = decode_archive(data)
    # Simulate an early v2 archive: singular package only, no packages[] / package_slug.
    legacy = {
        "format": "integral-portable",
        "version": 2,
        "mode": raw["mode"],
        "kind": "app",
        "apps": [
            {
                **{k: v for k, v in raw["apps"][0].items() if k != "package_slug"},
                "package_slug": "",
            }
        ],
        "tracks": [],
        "package": raw.get("package") or raw["packages"][0],
    }
    legacy_bytes = encode_archive(legacy, files)
    archive, _ = await read_archive(legacy_bytes)
    assert len(archive.packages) == 1
    assert archive.apps[0].package_slug == "portable-legacy"

    dest = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Legacy Dest",
        name_fold="legacy dest",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    await catalog_workspace(dest)
    profile = await get_user_node(test_user.id)
    if profile is not None:
        await profile.connect(dest, edge=IS_MEMBER_OF, role="owner")
    result = await import_resources(
        user_id=test_user.id, workspace_id=dest.id, data=legacy_bytes
    )
    assert result["apps"] == 1
    assert result["package"]["slug"] == "portable-legacy"
    imported = await App.get(result["resource_ids"][0])
    assert imported.lifecycle_state == "active"


@pytest.mark.asyncio
async def test_export_refuses_same_slug_fingerprint_conflict(
    tmp_path, test_user, monkeypatch
):
    packages = tmp_path / "packages"
    packages.mkdir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(packages))

    bundle_a = _write_demo_bundle(packages, slug="portable-conflict-a")
    bundle_b = _write_demo_bundle(packages, slug="portable-conflict-b")
    lib_a = await _admit_bundle(bundle_a)
    lib_b = await _admit_bundle(bundle_b)
    ws = await ensure_personal_workspace_for_user_id(test_user.id)
    first = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib_a.id,
        actor_id=test_user.id,
        include_seed_data=False,
        visibility="private",
        name_override="Conflict A",
    )
    second = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib_b.id,
        actor_id=test_user.id,
        include_seed_data=False,
        visibility="private",
        name_override="Conflict B",
    )
    assert first["app_id"] != second["app_id"]

    async def _conflicting_pack(app):
        slug = "shared-conflict-slug"
        prefix = member_prefix_for_slug(slug)
        # Distinct fingerprints for the same slug across the two selected Apps.
        fingerprint = ("a" if app.id == first["app_id"] else "b") * 64
        return (
            PackagePayload(
                slug=slug,
                version="1.0.0",
                fingerprint=fingerprint,
                member_prefix=prefix,
            ),
            {f"{prefix}operational-model.yaml": fingerprint.encode()},
        )

    monkeypatch.setattr(
        "app.services.portable_transfer._export_app_package", _conflicting_pack
    )
    with pytest.raises(BadRequestError, match="conflicting contents"):
        await export_resources(
            user_id=test_user.id,
            workspace_id=ws.id,
            selection=ExportSelection(
                kind="app",
                ids=[first["app_id"], second["app_id"]],
                mode="configuration",
            ),
        )


@pytest.mark.asyncio
async def test_protected_fields_still_blocked_without_portable_context(
    tmp_path, test_user, monkeypatch
):
    from app.services.app_graph import get_track_attached_operational_model
    from app.services.entry_create import create_entry_in_track

    packages = tmp_path / "packages"
    packages.mkdir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(packages))
    bundle = _write_demo_bundle(packages, slug="portable-protected", protected=True)
    library = await _admit_bundle(bundle)
    ws = await ensure_personal_workspace_for_user_id(test_user.id)
    installed = await install_app(
        workspace_id=ws.id,
        library_cp_id=library.id,
        actor_id=test_user.id,
        include_seed_data=False,
        visibility="private",
    )
    app = await App.get(installed["app_id"])
    track = (await app.nodes(edge=[CONTAINS], node=["Track"]))[0]
    cp = await get_track_attached_operational_model(track)
    types = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    with pytest.raises(BadRequestError, match="managed by its App"):
        await create_entry_in_track(
            track=track,
            user_id=test_user.id,
            workspace_id=ws.id,
            title="Blocked",
            type_id=types[0].id,
            custom_fields={"qty_on_hand": 5},
        )


@pytest.mark.asyncio
async def test_packaged_import_requires_app_owned_restore_validation(
    tmp_path, test_user, monkeypatch
):
    from app.services.app_graph import get_track_attached_operational_model
    from app.services.attachment_metadata.service import get_metadata_extraction_service

    packages = tmp_path / "packages"
    packages.mkdir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(packages))
    monkeypatch.setattr(
        get_metadata_extraction_service(), "schedule_extraction", lambda *_: None
    )

    bundle = _write_demo_bundle(packages, slug="portable-ledger", protected=True)
    library = await _admit_bundle(bundle)
    source_ws = await ensure_personal_workspace_for_user_id(test_user.id)
    installed = await install_app(
        workspace_id=source_ws.id,
        library_cp_id=library.id,
        actor_id=test_user.id,
        include_seed_data=False,
        visibility="private",
    )
    app = await App.get(installed["app_id"])
    track = (await app.nodes(edge=[CONTAINS], node=["Track"]))[0]
    cp = await get_track_attached_operational_model(track)
    types = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    # Ordinary synthetic protected state, written directly by this test fixture.
    from app.models.nodes import Entry

    entry = await Entry.create(
        track_id=track.id,
        type_id=types[0].id,
        title="Shelf A",
        author_id=test_user.id,
        custom_fields={"body": "bin", "qty_on_hand": 12},
    )
    await track.connect(entry, edge=CONTAINS)

    data = await export_resources(
        user_id=test_user.id,
        workspace_id=source_ws.id,
        selection=ExportSelection(kind="app", ids=[app.id], mode="full"),
    )
    dest = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Ledger Dest",
        name_fold="ledger dest",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    await catalog_workspace(dest)
    profile = await get_user_node(test_user.id)
    if profile is not None:
        await profile.connect(dest, edge=IS_MEMBER_OF, role="owner")
    for include_data in (True, False):
        with pytest.raises(BadRequestError, match="App-owned restore validation"):
            await import_resources(
                user_id=test_user.id,
                workspace_id=dest.id,
                data=data,
                include_data=include_data,
            )
    assert not await App.find({"context.workspace_id": dest.id})


@pytest.mark.asyncio
async def test_packaged_import_applies_settings_defaults(
    tmp_path, test_user, monkeypatch
):
    from app.services.attachment_metadata.service import get_metadata_extraction_service

    packages = tmp_path / "packages"
    packages.mkdir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(packages))
    monkeypatch.setattr(
        get_metadata_extraction_service(), "schedule_extraction", lambda *_: None
    )

    bundle = _write_demo_bundle(packages, slug="portable-settings", with_settings=True)
    library = await _admit_bundle(bundle)
    source_ws = await ensure_personal_workspace_for_user_id(test_user.id)
    installed = await install_app(
        workspace_id=source_ws.id,
        library_cp_id=library.id,
        actor_id=test_user.id,
        settings={"allow_negative_stock": True, "default_currency": "USD"},
        include_seed_data=False,
        visibility="private",
    )
    assert installed["status"] == "active"
    app = await App.get(installed["app_id"])
    assert app.settings.get("allow_negative_stock") is True
    assert app.settings.get("default_currency") == "USD"

    data = await export_resources(
        user_id=test_user.id,
        workspace_id=source_ws.id,
        selection=ExportSelection(kind="app", ids=[app.id], mode="configuration"),
    )
    dest = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Settings Dest",
        name_fold="settings dest",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    await catalog_workspace(dest)
    profile = await get_user_node(test_user.id)
    if profile is not None:
        await profile.connect(dest, edge=IS_MEMBER_OF, role="owner")
    result = await import_resources(
        user_id=test_user.id, workspace_id=dest.id, data=data
    )
    imported = await App.get(result["resource_ids"][0])
    assert imported.lifecycle_state == "active"
    assert imported.settings.get("allow_negative_stock") is True
    assert imported.settings.get("default_currency") == "USD"
