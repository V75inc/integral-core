"""A startup query backfill cannot invalidate an installed App contract."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError
from app.services.hooks.install_hook import _heal_stripped_operational_layer


@pytest.mark.asyncio
async def test_invalid_query_backfill_preserves_installed_profile(monkeypatch):
    from app.models.nodes import OperationalModel
    from app.services import operational_model_runtime

    before = {
        "scope": "app",
        "app": {
            "queries": [{"key": "current", "output_schema": {"typed": True}}],
            "home": {"title": "Existing home"},
        },
    }
    saved = []

    async def save():
        saved.append(True)

    cp = SimpleNamespace(manifest=deepcopy(before), save=save)

    async def source(_id):
        return SimpleNamespace(
            manifest={"app": {"queries": [{"key": "current", "output_schema": {}}]}}
        )

    def compile_candidate(*, manifest):
        assert manifest["app"]["queries"][0]["output_schema"] == {}
        raise BadRequestError(
            message="app.home summary field must be explicitly declared as scalar"
        )

    monkeypatch.setattr(OperationalModel, "get", source)
    monkeypatch.setattr(
        operational_model_runtime, "compile_canonical_manifest", compile_candidate
    )
    app = SimpleNamespace(id="app", installed_from_library_id="library")
    assert await _heal_stripped_operational_layer(app, cp) is False
    assert cp.manifest == before
    assert saved == []
