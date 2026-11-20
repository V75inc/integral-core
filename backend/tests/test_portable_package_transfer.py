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
                "key": "echo",
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
                "operations": ["echo"],
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
    monkeypatch.setattr(
        "app.services.package_paths.admitted_packages_root", lambda: packages
    )

    monkeypatch.setattr(
        get_metadata_extraction_service(), "schedule_extraction", lambda *_: None
    )

    bundle = _write_demo_bundle(packages)
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


@pytest.mark.asyncio
async def test_multi_packaged_app_export_import(tmp_path, test_user, monkeypatch):
    from app.services.attachment_metadata.service import get_metadata_extraction_service
    from app.services.entry_create import create_entry_in_track

    packages = tmp_path / "packages"
    packages.mkdir()
    monkeypatch.setattr(
        "app.services.package_paths.admitted_packages_root", lambda: packages
    )
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
async def test_mixed_packaged_and_declarative_export_import(
    tmp_path, test_user, monkeypatch
):
    from app.services.app_service import create_app_for_user
    from app.services.attachment_metadata.service import get_metadata_extraction_service

    packages = tmp_path / "packages"
    packages.mkdir()
    monkeypatch.setattr(
        "app.services.package_paths.admitted_packages_root", lambda: packages
    )
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
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=source_ws.id,
        selection=ExportSelection(
            kind="app",
            ids=[packaged["app_id"], declarative.id],
            mode="configuration",
        ),
    )
    raw, _files = decode_archive(data)
    assert raw["version"] == 2
    assert len(raw["packages"]) == 1
    by_name = {app["name"]: app for app in raw["apps"]}
    assert by_name["Packaged Side"]["package_slug"] == "portable-mixed-pkg"
    assert by_name["Declarative Side"]["package_slug"] == ""

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
        user_id=test_user.id, workspace_id=dest.id, data=data
    )
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
    monkeypatch.setattr(
        "app.services.package_paths.admitted_packages_root", lambda: packages
    )
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
    monkeypatch.setattr(
        "app.services.package_paths.admitted_packages_root", lambda: packages
    )

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
    monkeypatch.setattr(
        "app.services.package_paths.admitted_packages_root", lambda: packages
    )
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
async def test_packaged_import_restores_protected_fields(
    tmp_path, test_user, monkeypatch
):
    from app.services.app_graph import get_track_attached_operational_model
    from app.services.attachment_metadata.service import get_metadata_extraction_service
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_import_reservations import portable_import_writes

    packages = tmp_path / "packages"
    packages.mkdir()
    monkeypatch.setattr(
        "app.services.package_paths.admitted_packages_root", lambda: packages
    )
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
    with portable_import_writes():
        entry = await create_entry_in_track(
            track=track,
            user_id=test_user.id,
            workspace_id=source_ws.id,
            title="Shelf A",
            type_id=types[0].id,
            custom_fields={"body": "bin", "qty_on_hand": 12},
        )
    assert entry.custom_fields.get("qty_on_hand") == 12

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
    result = await import_resources(
        user_id=test_user.id, workspace_id=dest.id, data=data
    )
    assert result["entries"] == 1
    imported = await App.get(result["resource_ids"][0])
    imported_tracks = await imported.nodes(edge=[CONTAINS], node=["Track"])
    imported_entries = await imported_tracks[0].nodes(edge=[CONTAINS], node=["Entry"])
    match = next(row for row in imported_entries if row.title == "Shelf A")
    assert match.custom_fields.get("qty_on_hand") == 12


@pytest.mark.asyncio
async def test_packaged_import_applies_settings_defaults(
    tmp_path, test_user, monkeypatch
):
    from app.services.attachment_metadata.service import get_metadata_extraction_service

    packages = tmp_path / "packages"
    packages.mkdir()
    monkeypatch.setattr(
        "app.services.package_paths.admitted_packages_root", lambda: packages
    )
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
