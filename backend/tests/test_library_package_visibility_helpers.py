"""Unit tests for private library package workspace visibility helpers."""

from types import SimpleNamespace

from app.api.operational_models import (
    assert_library_package_installable_in_workspace,
    is_library_package_visible_for_workspace,
    library_package_owner_workspace_id,
)
from app.api.errors import ResourceNotFoundError
import pytest


def test_platform_package_visible_everywhere():
    pkg = SimpleNamespace(workspace_id=None)
    assert library_package_owner_workspace_id(pkg) is None
    assert is_library_package_visible_for_workspace(pkg, "ws-a")
    assert is_library_package_visible_for_workspace(pkg, None)


def test_private_package_only_in_owner_workspace():
    pkg = SimpleNamespace(workspace_id="ws-a")
    assert library_package_owner_workspace_id(pkg) == "ws-a"
    assert is_library_package_visible_for_workspace(pkg, "ws-a")
    assert not is_library_package_visible_for_workspace(pkg, "ws-b")
    assert not is_library_package_visible_for_workspace(pkg, None)


@pytest.mark.asyncio
async def test_install_rejects_cross_workspace_private_package():
    pkg = SimpleNamespace(workspace_id="ws-a", id="lib-1")
    with pytest.raises(ResourceNotFoundError):
        await assert_library_package_installable_in_workspace(pkg, "ws-b")
    await assert_library_package_installable_in_workspace(pkg, "ws-a")
    await assert_library_package_installable_in_workspace(
        SimpleNamespace(workspace_id=""), "ws-b"
    )
