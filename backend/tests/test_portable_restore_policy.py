"""Unit-level restore policy checks using ordinary bundles and mocked failures."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import BadRequestError
from app.schemas.portable_transfer import PortableApp, PortableArchive
from app.services.portable_transfer import _export_settings, _order_archive_apps

pytestmark = pytest.mark.unit


def test_v1_wire_shape_has_no_v2_fields():
    for kind in ("app", "track"):
        archive = PortableArchive(
            kind=kind,
            mode="configuration",
            apps=[PortableApp(ref="a", name="A")] if kind == "app" else [],
        )
        raw = archive.wire_dict()
        assert set(raw) == {"format", "version", "mode", "kind", "apps", "tracks"}
        for app in raw["apps"]:
            assert set(app) == {
                "ref",
                "model_ref",
                "name",
                "description",
                "icon",
                "accent_color",
                "manifest",
                "tags",
                "dashboards",
                "home_composition",
                "tracks",
            }


def test_settings_export_redacts_nested_secrets_and_undeclared_values():
    schema = {
        "properties": {
            "visible": {"type": "boolean"},
            "secret": {"writeOnly": True},
            "nested": {
                "properties": {
                    "password": {"format": "password"},
                    "label": {"type": "string"},
                }
            },
        }
    }
    assert _export_settings(
        {
            "visible": False,
            "secret": "synthetic",
            "unknown": "omit",
            "nested": {"password": "synthetic", "label": "Keep"},
        },
        schema,
    ) == {"visible": False, "nested": {"label": "Keep"}}


def test_dependency_order_uses_installed_app_name_aliases():
    provider = PortableApp(
        ref="provider", name="Shared Records", package_slug="records"
    )
    consumer = PortableApp(
        ref="consumer",
        name="Consumer",
        manifest={"app": {"requires_apps": [{"key": "shared-records"}]}},
    )
    assert [app.ref for app in _order_archive_apps([consumer, provider])] == [
        "provider",
        "consumer",
    ]


def test_package_fingerprint_uses_the_loader_order(tmp_path: Path):
    from app.services.operational_model_loader import compute_bundle_fingerprint
    from app.services.portable_package import pack_bundle_dir

    bundle = tmp_path / "ordinary"
    (bundle / "tools").mkdir(parents=True)
    (bundle / "operational-model.yaml").write_text("package: {slug: ordinary}\n")
    (bundle / "tools.py").write_text("# ordinary module\n")
    (bundle / "tools" / "hello.py").write_text("# ordinary module\n")
    _, fingerprint = pack_bundle_dir(bundle, "ordinary")
    assert fingerprint == compute_bundle_fingerprint(bundle)


@pytest.mark.asyncio
async def test_preflight_rejects_missing_restore_validator_before_install(monkeypatch):
    from app.services.portable_preflight import preflight_packaged_apps

    monkeypatch.setattr(
        "app.services.app_install.find_all_bundle_installs", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "app.services.operational_model_compile.compile_canonical_manifest",
        lambda **_: {
            "package": {"slug": "ordinary"},
            "app": {"protected_state": {"record": {"top_level": ["status"]}}},
        },
    )
    library = SimpleNamespace(id="library", name="Ordinary", manifest={})
    with pytest.raises(BadRequestError, match="App-owned restore validation"):
        await preflight_packaged_apps(
            [PortableApp(ref="app", name="Ordinary", package_slug="ordinary")],
            {"ordinary": library},
            workspace_id="disposable",
        )


@pytest.mark.asyncio
async def test_import_context_does_not_bypass_protected_status(monkeypatch):
    from app.services.app_invariant_guards import enforce_protected_field_write

    monkeypatch.setattr(
        "app.services.app_invariant_guards._canonical_protected_state",
        AsyncMock(return_value={"record": {"top_level": ["status"]}}),
    )
    monkeypatch.setattr(
        "app.services.app_operation_authority.permits_mutation",
        AsyncMock(return_value=False),
    )
    with pytest.raises(BadRequestError, match="managed by its App"):
        await enforce_protected_field_write(
            workspace_id="disposable",
            entry_type_key="record",
            proposed_custom_fields={},
            proposed_fields={"status": "active"},
            track_id="track",
        )


@pytest.mark.asyncio
async def test_skipped_record_removes_only_its_files_and_thumbnail(monkeypatch):
    from app.schemas.portable_transfer import PortableEntry, PortableFile
    from app.services.portable_transfer import _drop_reserved_import_entry

    attachment = SimpleNamespace(
        storage_key="owned-file",
        thumb_storage_key="owned-thumbnail",
        delete=AsyncMock(),
    )
    entry = SimpleNamespace(disconnect=AsyncMock(), delete=AsyncMock())
    storage = SimpleNamespace(delete_attachment=AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.services.portable_transfer.Entry.get", AsyncMock(return_value=entry)
    )
    monkeypatch.setattr(
        "app.services.portable_transfer._bounded", AsyncMock(return_value=[attachment])
    )
    monkeypatch.setattr(
        "app.services.portable_transfer.get_attachment_storage_service", lambda: storage
    )
    item = PortableEntry(
        ref="record",
        type_ref="type",
        attachments=[
            PortableFile(
                ref="attachment",
                filename="ordinary.txt",
                mime_type="text/plain",
                source_type="file",
            )
        ],
    )
    mapping = {
        "record": "new-record",
        "attachment": "new-file",
        "unrelated": "preserved",
    }
    keys = ["owned-file", "owned-thumbnail", "unrelated-file"]
    await _drop_reserved_import_entry(item=item, mapping=mapping, stored_keys=keys)
    assert mapping == {"unrelated": "preserved"}
    assert keys == ["unrelated-file"]
    assert [call.args[0] for call in storage.delete_attachment.await_args_list] == [
        "owned-file",
        "owned-thumbnail",
    ]


@pytest.mark.asyncio
async def test_package_restore_requires_destination_admission(monkeypatch):
    from app.schemas.portable_transfer import PackagePayload
    from app.services.portable_package import (
        fingerprint_members,
        resolve_admitted_package,
    )

    prefix = "package/ordinary/"
    files = {prefix + "operational-model.yaml": b"package: {slug: ordinary}\n"}
    payload = PackagePayload(
        slug="ordinary",
        fingerprint=fingerprint_members(files, prefix),
        member_prefix=prefix,
    )
    monkeypatch.setattr(
        "app.models.nodes.OperationalModel.find", AsyncMock(return_value=[])
    )
    with pytest.raises(BadRequestError, match="admitted by the destination host"):
        await resolve_admitted_package(
            payload, files, user_id="caller", workspace_id="disposable"
        )


@pytest.mark.asyncio
async def test_creation_authority_is_checked_before_package_resolution(monkeypatch):
    from app.api.errors import InsufficientPermissionsError
    from app.schemas.portable_transfer import PackagePayload
    from app.services.portable_transfer import import_resources

    archive = PortableArchive(
        version=2,
        kind="app",
        mode="configuration",
        apps=[PortableApp(ref="app", name="Ordinary")],
        packages=[
            PackagePayload(
                slug="ordinary", fingerprint="a" * 64, member_prefix="package/ordinary/"
            )
        ],
    )
    resolver = AsyncMock()
    monkeypatch.setattr(
        "app.services.portable_transfer.read_archive",
        AsyncMock(return_value=(archive, {})),
    )
    monkeypatch.setattr(
        "app.services.permissions.can_create_app_under_workspace",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "app.services.portable_package.resolve_admitted_package", resolver
    )
    with pytest.raises(InsufficientPermissionsError):
        await import_resources(user_id="caller", workspace_id="disposable", data=b"")
    resolver.assert_not_awaited()
