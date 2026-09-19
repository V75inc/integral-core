"""Contract: Asset Register custom asset detail extension view (WP-06)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.app_extension_views import serve_extension_view_asset
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.content_profile_runtime import compile_canonical_manifest

REPO = Path(__file__).resolve().parents[3]
ASSET_APP = REPO / "examples" / "asset-register"


@pytest.fixture
def asset_register_root(monkeypatch):
    assert ASSET_APP.is_dir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(ASSET_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    from app.services.content_profile_library_sync import (
        reset_library_profiles_cache_for_testing,
    )

    reset_library_profiles_cache_for_testing()
    yield ASSET_APP
    reset_library_profiles_cache_for_testing()


def _app_stub(app_id: str, workspace_id: str):
    return type(
        "AppStub",
        (),
        {
            "id": app_id,
            "workspace_id": workspace_id,
            "lifecycle_state": "active",
            "metadata": {"bundle_dir_path": str(ASSET_APP)},
            "installed_package_version": "1.0.0",
        },
    )()


@pytest.mark.contract
def test_asset_register_compiles_asset_detail_view(asset_register_root):
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(asset_register_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "asset-register")
    canonical = compile_canonical_manifest(manifest=spec.manifest)

    ext_views = (canonical.get("app") or {}).get("extension_views") or []
    by_key = {v.get("key"): v for v in ext_views}
    assert "asset_detail" in by_key
    assert by_key["asset_detail"]["entry"] == "views/asset_detail/index.html"

    assets_track = next(
        t
        for t in (canonical.get("app") or {}).get("tracks") or []
        if t.get("key") == "assets"
    )
    panel = next(
        v
        for v in (assets_track.get("views") or [])
        if v.get("key") == "asset_detail_panel"
    )
    assert panel.get("view_type") == "extension_view"
    assert panel.get("extension_view_key") == "asset_detail"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_asset_detail_index_html_served(asset_register_root):
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(asset_register_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "asset-register")
    canonical = compile_canonical_manifest(manifest=spec.manifest)
    ws = "ws-asset-detail"
    app_id = "n.App.asset-register"

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
            view_key="asset_detail",
            asset_path="index.html",
        )

    assert file_path.is_file()
    assert "html" in media_type
    body = file_path.read_text(encoding="utf-8")
    assert "available_assets" in body
    assert "check_out_asset" in body
    assert "requestQuery" in body
