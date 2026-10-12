"""Independent signed current anchors and mandatory restored quarantine."""

import json

import pytest

from app import local_runtime
from app.services import access_recovery


@pytest.mark.asyncio
async def test_current_anchor_rejects_stale_foreign_and_modified_checkpoint(
    tmp_path, monkeypatch
):
    """Only externally signed latest state can qualify authorization replay."""

    async def inventory():
        return {"node": [], "edge": [], "object": []}

    monkeypatch.setattr(access_recovery, "inventory", inventory)
    directory = tmp_path / "external"
    first = await access_recovery.checkpoint(
        installation_id="one", directory=directory, home=tmp_path / "home"
    )
    second = await access_recovery.checkpoint(
        installation_id="one", directory=directory, home=tmp_path / "home"
    )
    assert second["generation"] == first["generation"] + 1
    assert access_recovery.current_checkpoint(directory, "one")["generation"] == 2
    with pytest.raises(ValueError):
        access_recovery.current_checkpoint(directory, "foreign")
    latest = json.loads((directory / "latest.json").read_text())
    latest["file"] = first["file"]
    local_runtime.private_json(directory / "latest.json", latest)
    with pytest.raises(ValueError):
        access_recovery.current_checkpoint(directory, "one")


def test_restore_quarantine_refuses_status_only_review(tmp_path):
    """A status label alone cannot re-enable a recovered runtime."""
    config = local_runtime.initialize(tmp_path)
    local_runtime.private_json(
        tmp_path / "recovery.json",
        {"status": "reviewed", "installation_id": config["installation_id"]},
    )
    with pytest.raises(local_runtime.RuntimeErrorDetail, match="quarantined"):
        local_runtime._require_recovery_review(tmp_path)
