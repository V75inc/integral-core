"""Contract: app extension view host + asset delivery (ADR-011 WP-03)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from jvspatial.api.exceptions import InsufficientPermissionsError

from app.services.app_extension_views import (
    list_extension_views,
    serve_extension_view_asset,
    verify_handshake_token,
)
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.content_profile_runtime import compile_canonical_manifest

REPO = Path(__file__).resolve().parents[3]
REF_APP = REPO / "examples" / "reference-hello-app"


@pytest.fixture
def reference_root(monkeypatch):
    assert REF_APP.is_dir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(REF_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    from app.services.content_profile_library_sync import (
        reset_library_profiles_cache_for_testing,
    )

    reset_library_profiles_cache_for_testing()
    yield REF_APP
    reset_library_profiles_cache_for_testing()


def _app_stub(app_id: str, workspace_id: str):
    return type(
        "AppStub",
        (),
        {
            "id": app_id,
            "workspace_id": workspace_id,
            "lifecycle_state": "active",
            "metadata": {"bundle_dir_path": str(REF_APP)},
            "installed_package_version": "1.1.0",
        },
    )()


@pytest.mark.contract
def test_reference_hello_compiles_extension_views(reference_root):
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(reference_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "reference-hello-app")
    canonical = compile_canonical_manifest(manifest=spec.manifest)

    ext_views = (canonical.get("app") or {}).get("extension_views") or []
    by_key = {v.get("key"): v for v in ext_views}
    assert "hello_panel" in by_key
    assert by_key["hello_panel"]["entry"] == "views/hello_panel/index.html"

    tracks = (canonical.get("app") or {}).get("tracks") or []
    notes = next(t for t in tracks if t.get("key") == "notes")
    panel = next(
        v for v in (notes.get("views") or []) if v.get("key") == "notes_hello_panel"
    )
    assert panel.get("view_type") == "extension_view"
    assert panel.get("extension_view_key") == "hello_panel"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_list_extension_views_mints_handshake(reference_root):
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(reference_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "reference-hello-app")
    canonical = compile_canonical_manifest(manifest=spec.manifest)
    ws = "ws-ext-views"
    app_id = "n.App.ext-hello"

    with (
        patch(
            "app.services.app_extension_views.App.get",
            new=AsyncMock(return_value=_app_stub(app_id, ws)),
        ),
        patch(
            "app.services.app_extension_views.resolve_role",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_extension_views.can_access_workspace",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_extension_views._compiled_app_manifest",
            new=AsyncMock(return_value=canonical),
        ),
    ):
        result = await list_extension_views(
            user_id="u1",
            workspace_id=ws,
            app_id=app_id,
            mount_id="mount-1",
            view_key="hello_panel",
        )

    assert result["protocol"] == "integral.extension.v1"
    assert result["views"][0]["key"] == "hello_panel"
    payload = verify_handshake_token(result["handshake_token"])
    assert payload["workspace_id"] == ws
    assert payload["app_id"] == app_id
    assert payload["view_key"] == "hello_panel"
    assert payload["package_version"] == "1.1.0"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_extension_view_routes_deny_user_outside_workspace(reference_root):
    """The iframe handshake and static assets share the App/workspace gate."""
    ws = "ws-ext-views"
    app_id = "n.App.ext-hello"

    with (
        patch(
            "app.services.app_extension_views.App.get",
            new=AsyncMock(return_value=_app_stub(app_id, ws)),
        ),
        patch(
            "app.services.app_extension_views.can_access_workspace",
            new=AsyncMock(return_value="none"),
        ),
    ):
        with pytest.raises(InsufficientPermissionsError, match="Access denied"):
            await list_extension_views(
                user_id="outside-user",
                workspace_id=ws,
                app_id=app_id,
                mount_id="mount-denied",
                view_key="hello_panel",
            )

        with pytest.raises(InsufficientPermissionsError, match="Access denied"):
            await serve_extension_view_asset(
                user_id="outside-user",
                workspace_id=ws,
                app_id=app_id,
                view_key="hello_panel",
                asset_path="index.html",
            )


@pytest.mark.contract
@pytest.mark.asyncio
async def test_serve_extension_view_index_html(reference_root):
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(reference_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "reference-hello-app")
    canonical = compile_canonical_manifest(manifest=spec.manifest)
    ws = "ws-ext-views"
    app_id = "n.App.ext-hello"

    with (
        patch(
            "app.services.app_extension_views.App.get",
            new=AsyncMock(return_value=_app_stub(app_id, ws)),
        ),
        patch(
            "app.services.app_extension_views.resolve_role",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_extension_views.can_access_workspace",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_extension_views._compiled_app_manifest",
            new=AsyncMock(return_value=canonical),
        ),
    ):
        file_path, media_type = await serve_extension_view_asset(
            user_id="u1",
            workspace_id=ws,
            app_id=app_id,
            view_key="hello_panel",
            asset_path="index.html",
        )

    assert file_path.is_file()
    assert file_path.name == "index.html"
    assert "html" in media_type
    body = file_path.read_text(encoding="utf-8")
    assert "integral.extension.v1" in body
