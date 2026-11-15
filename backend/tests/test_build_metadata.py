"""The settings readout reports the installed Core distribution version."""

from importlib import metadata
from unittest.mock import patch

import pytest

from app.api.meta import _core_version, build_meta


@pytest.fixture(autouse=True)
def fresh_build_version_cache():
    # Each test models a distinct installed distribution/process.
    _core_version.cache_clear()
    yield
    _core_version.cache_clear()


@pytest.mark.asyncio
async def test_build_metadata_reports_installed_core_version():
    with patch("app.api.meta.metadata.version", return_value="0.1.1rc31") as version:
        result = await build_meta()

    version.assert_called_once_with("integral-core")
    assert result["core_version"] == "0.1.1rc31"
    assert "asset_version" in result


@pytest.mark.asyncio
async def test_build_metadata_handles_missing_distribution():
    with patch(
        "app.api.meta.metadata.version", side_effect=metadata.PackageNotFoundError
    ):
        result = await build_meta()

    assert result["core_version"] is None
    assert "asset_version" in result


@pytest.mark.asyncio
async def test_build_metadata_reuses_version_within_running_process():
    with patch("app.api.meta.metadata.version", return_value="0.1.1rc63") as version:
        first = await build_meta()
        second = await build_meta()
    version.assert_called_once_with("integral-core")
    assert first["core_version"] == second["core_version"] == "0.1.1rc63"
