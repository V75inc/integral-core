"""Native code requires current operator trust, exact content and App authority."""

import base64
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nacl.signing import SigningKey

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
from app.schemas.ui_components import UIComponentDeclaration
from app.services import ui_components as service


@pytest.fixture
def release(tmp_path, monkeypatch):
    service._mount_candidates.clear()
    bundle = tmp_path / "sample"
    (bundle / "ui").mkdir(parents=True)
    source = bundle / "ui/component.mjs"
    content = b"export default function Component(){return null}"
    source.write_bytes(content)
    component = UIComponentDeclaration(
        key="editor",
        entry="ui/component.mjs",
        digest=hashlib.sha256(content).hexdigest(),
        placements=["track"],
    ).model_dump()
    signing = SigningKey.generate()
    monkeypatch.setattr(service.settings, "DEBUG", False)
    monkeypatch.setattr(
        service.settings,
        "INTEGRAL_UI_PUBLISHERS",
        json.dumps(
            {
                "Example": {
                    "public_key": base64.b64encode(bytes(signing.verify_key)).decode(),
                    "packages": ["sample"],
                }
            }
        ),
    )
    monkeypatch.setattr(
        service.settings, "INTEGRAL_UI_ENABLED_COMPONENTS", "sample:editor"
    )
    monkeypatch.setattr(
        service.settings, "INTEGRAL_UI_ARTIFACT_ROOT", str(tmp_path / "cache")
    )

    def sign(declaration=component):
        source.with_name(f"{source.stem}.{declaration['key']}.sig").write_text(
            base64.b64encode(
                signing.sign(
                    service.release_payload("Example", "sample", declaration)
                ).signature
            ).decode()
        )

    sign()
    return bundle, component, content, sign


def test_signed_artifact_is_immutable_and_trust_is_rechecked(release, monkeypatch):
    bundle, component, content, _ = release
    url = service._admit(bundle, "Example", "sample", component)
    assert component["digest"] in url
    (bundle / component["entry"]).write_bytes(b"changed after admission")
    assert service.read_artifact("sample", component["digest"]) == content
    assert service._admit(bundle, "Example", "sample", component) == url
    monkeypatch.setattr(service.settings, "INTEGRAL_UI_PUBLISHERS", "{}")
    with pytest.raises(InsufficientPermissionsError):
        service.read_artifact("sample", component["digest"])


@pytest.mark.parametrize(
    "change", ["bytes", "signature", "publisher", "package", "export", "disabled"]
)
def test_rejects_tampering_and_unadmitted_publishers(release, monkeypatch, change):
    bundle, component, _, _ = release
    publisher, slug = "Example", "sample"
    if change == "bytes":
        (bundle / component["entry"]).write_bytes(b"bad")
    if change == "signature":
        (bundle / component["entry"]).with_name("component.editor.sig").unlink()
    if change == "publisher":
        publisher = "Other"
    if change == "package":
        slug = "other"
    if change == "export":
        component["export_name"] = "Other"
    if change == "disabled":
        service._admit(bundle, publisher, slug, component)
        monkeypatch.setattr(service.settings, "INTEGRAL_UI_ENABLED_COMPONENTS", "")
        with pytest.raises(InsufficientPermissionsError):
            service.read_artifact(slug, component["digest"])
        return
    with pytest.raises((BadRequestError, InsufficientPermissionsError)):
        service._admit(bundle, publisher, slug, component)


def test_dev_override_requires_debug_and_explicit_operator_package(
    release, monkeypatch
):
    bundle, component, _, _ = release
    (bundle / component["entry"]).with_name("component.editor.sig").unlink()
    monkeypatch.setattr(service.settings, "INTEGRAL_UI_PUBLISHERS", "{}")
    monkeypatch.setattr(service.settings, "INTEGRAL_UI_DEVELOPMENT_PACKAGES", "sample")
    with pytest.raises(InsufficientPermissionsError):
        service._admit(bundle, "Example", "sample", component)
    monkeypatch.setattr(service.settings, "DEBUG", True)
    service._admit(bundle, "Example", "sample", component)
    assert service.read_artifact("sample", component["digest"])


def test_symlink_escape_and_unknown_artifacts_fail_closed(release, tmp_path):
    bundle, component, _, _ = release
    outside = tmp_path / "outside.mjs"
    outside.write_text("outside")
    source = bundle / component["entry"]
    source.unlink()
    source.symlink_to(outside)
    with pytest.raises(BadRequestError):
        service._admit(bundle, "Example", "sample", component)
    with pytest.raises(ResourceNotFoundError):
        service.read_artifact("../sample", component["digest"])
    with pytest.raises(ResourceNotFoundError):
        service.read_artifact("sample", "0" * 64)


def test_two_releases_without_host_rebuild_retain_old_artifacts(release):
    bundle, component, content, sign = release
    service._admit(bundle, "Example", "sample", component)
    next_release = dict(
        component, digest=hashlib.sha256(content + b"\n// release B").hexdigest()
    )
    (bundle / component["entry"]).write_bytes(content + b"\n// release B")
    sign(next_release)
    service._admit(bundle, "Example", "sample", next_release)
    assert service.read_artifact("sample", component["digest"]) == content
    assert service.read_artifact("sample", next_release["digest"]) != content


@pytest.mark.asyncio
async def test_mount_manifest_uses_current_workspace_not_a_cached_app(monkeypatch):
    from app.services import access_delegation

    manifest = {"package": {"slug": "sample"}}
    monkeypatch.setattr(
        access_delegation,
        "current_definition",
        AsyncMock(
            return_value=(
                {
                    "context": {
                        "workspace_id": "current",
                        "installed_package_slug": "sample",
                    }
                },
                {"context": {"canonical_manifest": manifest}},
            )
        ),
    )
    assert await service._active_manifest("app", workspace_id="current") == manifest
    with pytest.raises(InsufficientPermissionsError):
        await service._active_manifest("app", workspace_id="previous")


@pytest.mark.asyncio
async def test_warm_mount_rechecks_access_and_returns_current_revision(
    release, monkeypatch
):
    from app.models.nodes import App
    from app.services import app_extension_views as views

    bundle, component, _, _ = release
    app = SimpleNamespace(
        id="app", active_definition_revision=7, installed_package_slug="sample"
    )
    monkeypatch.setattr(App, "get", AsyncMock(return_value=app))
    access = AsyncMock()
    monkeypatch.setattr(views, "_ensure_app_access", access)
    manifest = service._ActiveManifest(
        package={"name": "sample", "publisher": "Example"},
        app={"ui_components": [component]},
    )
    manifest.definition_revision = "8"
    current = AsyncMock(return_value=manifest)
    monkeypatch.setattr(service, "_active_manifest", current)
    monkeypatch.setattr(views, "_resolve_bundle_dir", AsyncMock(return_value=bundle))
    args = dict(
        user_id="user",
        workspace_id="ws",
        app_id="app",
        key="editor",
        placement="track",
        props={},
    )
    await service.resolve_component(**args)
    access.reset_mock()
    current.reset_mock()
    result = await service.resolve_component(**args)
    assert result["definition_revision"] == "8"
    assert access.await_count == current.await_count == 1
    access.side_effect = InsufficientPermissionsError(message="revoked")
    with pytest.raises(InsufficientPermissionsError, match="revoked"):
        await service.resolve_component(**args)
    # An already-warm release cannot survive revoked publisher admission.
    access.side_effect = None
    monkeypatch.setattr(service.settings, "INTEGRAL_UI_PUBLISHERS", "{}")
    with pytest.raises(InsufficientPermissionsError):
        await service.resolve_component(**args)


@pytest.mark.asyncio
async def test_warm_candidate_never_selects_a_previous_active_release(
    release, monkeypatch
):
    from app.models.nodes import App
    from app.services import app_extension_views as views

    bundle, component, content, sign = release
    app = SimpleNamespace(
        id="app", active_definition_revision=7, installed_package_slug="sample"
    )
    monkeypatch.setattr(App, "get", AsyncMock(return_value=app))
    monkeypatch.setattr(views, "_ensure_app_access", AsyncMock())
    manifest = {
        "package": {"name": "sample", "publisher": "Example"},
        "app": {"ui_components": [component]},
    }
    current = AsyncMock(return_value=manifest)
    monkeypatch.setattr(service, "_active_manifest", current)
    monkeypatch.setattr(views, "_resolve_bundle_dir", AsyncMock(return_value=bundle))
    args = dict(
        user_id="user",
        workspace_id="ws",
        app_id="app",
        key="editor",
        placement="track",
        props={},
    )
    await service.resolve_component(**args)
    next_release = dict(
        component, digest=hashlib.sha256(content + b"\n// changed").hexdigest()
    )
    (bundle / component["entry"]).write_bytes(content + b"\n// changed")
    sign(next_release)
    current.return_value = dict(manifest, app={"ui_components": [next_release]})
    current.reset_mock()
    result = await service.resolve_component(**args)
    assert next_release["digest"] in result["artifact_url"]
    assert current.await_count <= 3


@pytest.mark.asyncio
async def test_mount_uses_active_definition_and_rechecks_access(release, monkeypatch):
    from app.models.nodes import App
    from app.services import app_extension_views as views

    bundle, component, _, _ = release
    app = SimpleNamespace(
        id="app", active_definition_revision=7, installed_package_slug="sample"
    )
    monkeypatch.setattr(App, "get", AsyncMock(return_value=app))
    access = AsyncMock()
    monkeypatch.setattr(views, "_ensure_app_access", access)
    manifest = {
        "package": {"name": "sample", "publisher": "Example"},
        "app": {"ui_components": [component]},
    }
    compiled = AsyncMock(return_value=manifest)
    monkeypatch.setattr(service, "_active_manifest", compiled)
    monkeypatch.setattr(views, "_resolve_bundle_dir", AsyncMock(return_value=bundle))
    args = dict(
        user_id="user",
        workspace_id="ws",
        app_id="app",
        key="editor",
        placement="track",
        props={},
    )
    result = await service.resolve_component(**args)
    assert result["enabled"] and result["definition_revision"] == "7"
    assert access.await_count == 2
    with pytest.raises(BadRequestError):
        await service.resolve_component(**dict(args, props={"undeclared": True}))
    with pytest.raises(BadRequestError):
        await service.resolve_component(**dict(args, placement="home"))
    with pytest.raises(ResourceNotFoundError):
        await service.resolve_component(**dict(args, key="absent"))
    access.side_effect = InsufficientPermissionsError(message="scope denied")
    with pytest.raises(InsufficientPermissionsError):
        await service.resolve_component(**dict(args, workspace_id="foreign"))
    # Publisher verification cannot preserve an admission after access was
    # revoked while the filesystem work was in progress.
    service._mount_candidates.clear()
    access.side_effect = [None, InsufficientPermissionsError(message="revoked")]
    with pytest.raises(InsufficientPermissionsError, match="revoked"):
        await service.resolve_component(**args)
    access.side_effect = None
    compiled.side_effect = [manifest, dict(manifest, app={"ui_components": []})]
    with pytest.raises(BadRequestError, match="definition changed"):
        await service.resolve_component(**args)


def test_home_reference_is_declared_and_upgrade_cannot_remove_it():
    from app.services.home_native import validate_native_home
    from app.services.operational_model_compile import compile_canonical_manifest

    component = UIComponentDeclaration(
        key="overview", entry="ui/overview.mjs", digest="a" * 64, placements=["home"]
    ).model_dump()
    home = {
        "title": "Home",
        "renderer": "native",
        "content": {
            "id": "overview",
            "type": "react_component",
            "component_key": "overview",
        },
    }
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "app": {"ui_components": [component], "home": home},
    }
    compiled = compile_canonical_manifest(manifest=manifest)
    assert compiled["app"]["home"]["content"]["component_key"] == "overview"
    validate_native_home(home, compiled)
    with pytest.raises(ValueError):
        validate_native_home(home, {"app": {"ui_components": []}})
    assert compile_canonical_manifest(manifest=compiled) == compiled


def test_javascript_only_bundle_requires_configured_bundle_signature(tmp_path):
    from app.services.operational_model_signature import verify_bundle_signature

    (tmp_path / "ui").mkdir()
    (tmp_path / "ui/editor.mjs").write_text("export default function View(){}")
    key = SigningKey.generate()
    result = verify_bundle_signature(
        tmp_path, base64.b64encode(bytes(key.verify_key)).decode()
    )
    assert not result.verified


def test_shared_module_keeps_independent_component_admission(release, monkeypatch):
    bundle, component, content, sign = release
    service._admit(bundle, "Example", "sample", component)
    other = dict(component, key="other", export_name="Other")
    sign(other)
    service._admit(bundle, "Example", "sample", other)
    monkeypatch.setattr(
        service.settings, "INTEGRAL_UI_ENABLED_COMPONENTS", "sample:editor"
    )
    assert service.read_artifact("sample", component["digest"]) == content


def test_rollback_can_readmit_a_retained_release_without_source(release):
    bundle, component, content, _ = release
    first = service._admit(bundle, "Example", "sample", component)
    (bundle / component["entry"]).unlink()
    assert service._admit(bundle, "Example", "sample", component) == first
    assert service.read_artifact("sample", component["digest"]) == content


def test_node_release_signer_matches_python_canonical_payload(
    release, monkeypatch, tmp_path
):
    import shutil
    import subprocess
    from pathlib import Path

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the SDK signer interoperability check")
    bundle, component, content, _ = release
    publisher = "Example Núcleo"
    private = Ed25519PrivateKey.generate()
    key_file = tmp_path / "synthetic-key.pem"
    key_file.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    monkeypatch.setattr(
        service.settings,
        "INTEGRAL_UI_PUBLISHERS",
        json.dumps(
            {
                publisher: {
                    "public_key": base64.b64encode(public).decode(),
                    "packages": ["sample"],
                }
            }
        ),
    )
    envelope = tmp_path / "release.json"
    envelope.write_text(
        json.dumps(
            {"publisher": publisher, "package": "sample", "component": component},
            ensure_ascii=False,
        )
    )
    signer = Path(__file__).resolve().parents[2] / "sdk/react/bin/sign-release.mjs"
    subprocess.run(
        [
            node,
            str(signer),
            str(envelope),
            str(key_file),
            str(bundle / component["entry"]),
        ],
        check=True,
        capture_output=True,
    )
    service._admit(bundle, publisher, "sample", component)
    assert service.read_artifact("sample", component["digest"]) == content


def test_native_entry_component_preserves_declarative_rollback():
    from app.services.operational_model_compile import _normalize_ui_contributions

    source = [
        {
            "placement": "entry_compose",
            "component_key": "editor",
            "fallback_view": "document",
            "owns_form": True,
            "layout": "wide",
        }
    ]
    normalized = _normalize_ui_contributions(source, where="test")
    assert normalized[0]["component_key"] == "editor"
    assert normalized[0]["fallback_view"] == "document"
    assert _normalize_ui_contributions(normalized, where="test") == normalized
