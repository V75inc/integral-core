"""W1.5: revision-bound verification reads the apply; it does not build again."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from app.services import build_verification as bv
from app.services.build_verification import (
    ReadDenied,
    ReadFailed,
    _Reader,
    build_item_mapping,
    make_execution_receipt,
    verify_build,
    verify_loaded,
)

pytestmark = pytest.mark.smoke


def _blueprint() -> Dict[str, Any]:
    return {
        "app": {"id": "app", "name": "Bike Repair"},
        "tracks": [
            {
                "id": "track.jobs",
                "name": "Jobs",
                "entry_types": [
                    {
                        "id": "type.job",
                        "name": "Job",
                        "fields": [
                            {
                                "id": "f.customer",
                                "key": "customer",
                                "name": "Customer",
                                "type": "text",
                            },
                            {
                                "id": "f.vehicle",
                                "key": "vehicle",
                                "name": "Vehicle",
                                "type": "relation",
                                "relation": {
                                    "target": "entry",
                                    "target_entry_types": ["Vehicle"],
                                },
                            },
                        ],
                    }
                ],
            }
        ],
        "views": [
            {
                "id": "view.board",
                "track": "track.jobs",
                "name": "Jobs board",
                "type": "kanban",
                "decision": "What to work on next",
            }
        ],
    }


def _applied() -> Dict[str, Any]:
    return {
        "results": [
            {
                "kind": "create_app",
                "result": {"app": {"id": "a1", "name": "Bike Repair"}},
            },
            {
                "kind": "create_track",
                "result": {"track": {"id": "t1", "title": "Jobs"}},
            },
            {"kind": "save_view", "result": {"view_id": "v1", "name": "Jobs board"}},
        ]
    }


class _FakeReader:
    def __init__(self, fail: str = "") -> None:
        self.fail = fail
        self.calls = 0
        self.relation = {"target": "entry"}

    async def read(self, locator: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        if self.fail == "denied":
            raise ReadDenied()
        if self.fail == "failed":
            raise ReadFailed()
        kind = locator["kind"]
        if kind == "app":
            return {"name": "Bike Repair"}
        if kind == "track":
            return {"name": "Jobs"}
        if kind == "entry_type":
            return {"name": "Job"}
        if kind == "field" and locator.get("field_key") == "vehicle":
            return {"key": "vehicle", "type": "relation", "relation": self.relation}
        if kind == "field":
            return {"key": locator.get("field_key"), "type": "text"}
        if kind == "view":
            return {"name": "Jobs board", "type": "kanban", "is_default": True}
        if kind == "platform_default":
            return {
                "track_ids": locator.get("track_ids") or [],
                "feed_track_ids": locator.get("track_ids") or [],
            }
        if kind == "skill":
            return {"private": True, "app_id": "a1"}
        raise AssertionError(kind)


def _receipt(blueprint: Dict[str, Any] | None = None) -> Dict[str, Any]:
    body = blueprint if blueprint is not None else _blueprint()
    return make_execution_receipt(
        design_id="d1",
        design_revision=1,
        blueprint_digest="abc",
        blueprint=body,
        batch_token="batch-1",
        execute_result=_applied(),
        applied_at="2026-09-26T00:00:00Z",
        user_turn=2,
    )


@pytest.mark.asyncio
async def test_minimal_app_without_optional_features_verifies():
    """A blueprint with no dashboard, seed, skill, or routine verifies."""
    blueprint = _blueprint()
    receipt = _receipt(blueprint)
    mapped = set(receipt["mapping"])
    assert "app" in mapped
    assert "track.jobs" in mapped
    assert "f.customer" in mapped
    assert "view.board" in mapped
    for optional in ("dashboard", "skill", "routine", "seed"):
        assert all(not item_id.startswith(optional) for item_id in mapped)

    result = await verify_loaded(
        blueprint=blueprint,
        design_id="d1",
        design_revision=1,
        receipt=receipt,
        reader=_FakeReader(),
    )
    assert result["status"] == "verified"
    assert {item["status"] for item in result["items"]} == {"present"}
    assert all(
        item["id"] not in {"dash", "skill.x", "routine.x"} for item in result["items"]
    )


@pytest.mark.asyncio
async def test_missing_promised_view_is_partial():
    """A view the design promised but the apply did not record is partial."""
    blueprint = _blueprint()
    receipt = _receipt(blueprint)
    del receipt["mapping"]["view.board"]
    result = await verify_loaded(
        blueprint=blueprint,
        design_id="d1",
        design_revision=1,
        receipt=receipt,
        reader=_FakeReader(),
    )
    view = next(item for item in result["items"] if item["id"] == "view.board")
    assert view["status"] == "missing"
    assert result["status"] == "partial"


@pytest.mark.asyncio
async def test_denied_read_is_not_missing_or_verified():
    """No access stays denied and blocks the result."""
    result = await verify_loaded(
        blueprint=_blueprint(),
        design_id="d1",
        design_revision=1,
        receipt=_receipt(),
        reader=_FakeReader("denied"),
    )
    assert result["status"] == "blocked"
    assert {item["status"] for item in result["items"]} == {"denied"}


@pytest.mark.asyncio
async def test_failed_read_is_not_missing_or_verified():
    """A broken read stays read_failed and fails the result."""
    result = await verify_loaded(
        blueprint=_blueprint(),
        design_id="d1",
        design_revision=1,
        receipt=_receipt(),
        reader=_FakeReader("failed"),
    )
    assert result["status"] == "failed"
    assert {item["status"] for item in result["items"]} == {"read_failed"}


@pytest.mark.asyncio
async def test_entry_relation_does_not_require_an_anchor():
    """An entry link is enough; the anchor form is not also required."""
    reader = _FakeReader()
    result = await verify_loaded(
        blueprint=_blueprint(),
        design_id="d1",
        design_revision=1,
        receipt=_receipt(),
        reader=reader,
    )
    field = next(item for item in result["items"] if item["id"] == "f.vehicle")
    assert field["status"] == "present"

    reader.relation = {"target": "track", "target_track_template": "notes"}
    result = await verify_loaded(
        blueprint=_blueprint(),
        design_id="d1",
        design_revision=1,
        receipt=_receipt(),
        reader=reader,
    )
    field = next(item for item in result["items"] if item["id"] == "f.vehicle")
    assert field["status"] == "mismatch"
    assert result["status"] == "partial"


@pytest.mark.asyncio
async def test_private_skill_is_checked_in_its_app():
    """A private skill must be private on the App this build created."""
    blueprint = _blueprint()
    blueprint["skills"] = [
        {
            "id": "skill.close",
            "name": "Close out",
            "purpose": "Close a finished job",
            "visibility": "app_private",
        }
    ]
    applied = _applied()
    applied["results"].append(
        {"kind": "author_skill", "result": {"skill_id": "s1", "key": "close_out"}}
    )
    receipt = make_execution_receipt(
        design_id="d1",
        design_revision=1,
        blueprint_digest="abc",
        blueprint=blueprint,
        batch_token="batch-1",
        execute_result=applied,
        applied_at="t",
        user_turn=1,
    )
    assert receipt["mapping"]["skill.close"]["object_id"] == "s1"
    result = await verify_loaded(
        blueprint=blueprint,
        design_id="d1",
        design_revision=1,
        receipt=receipt,
        reader=_FakeReader(),
    )
    skill = next(item for item in result["items"] if item["id"] == "skill.close")
    assert skill["status"] == "present"

    class _Public(_FakeReader):
        async def read(self, locator):
            snap = await super().read(locator)
            if locator["kind"] == "skill":
                return {"private": False, "app_id": "a1"}
            return snap

    result = await verify_loaded(
        blueprint=blueprint,
        design_id="d1",
        design_revision=1,
        receipt=receipt,
        reader=_Public(),
    )
    skill = next(item for item in result["items"] if item["id"] == "skill.close")
    assert skill["status"] == "mismatch"


@pytest.mark.asyncio
async def test_feed_platform_default_is_required_only_when_recorded():
    """A recorded Feed default is present only when every Track has one."""
    blueprint = _blueprint()
    blueprint["platform_defaults"] = [
        {"id": "default.feed", "kind": "view", "detail": "Every Track has a Feed."}
    ]
    result = await verify_loaded(
        blueprint=blueprint,
        design_id="d1",
        design_revision=1,
        receipt=_receipt(blueprint),
        reader=_FakeReader(),
    )
    feed = next(item for item in result["items"] if item["id"] == "default.feed")
    assert feed["status"] == "present"

    class _NoFeed(_FakeReader):
        async def read(self, locator):
            if locator["kind"] == "platform_default":
                return {
                    "track_ids": locator.get("track_ids") or [],
                    "feed_track_ids": [],
                }
            return await super().read(locator)

    result = await verify_loaded(
        blueprint=blueprint,
        design_id="d1",
        design_revision=1,
        receipt=_receipt(blueprint),
        reader=_NoFeed(),
    )
    feed = next(item for item in result["items"] if item["id"] == "default.feed")
    assert feed["status"] == "missing"
    assert result["status"] == "partial"


@pytest.mark.asyncio
async def test_wrong_revision_or_receipt_does_not_read(monkeypatch):
    """A changed revision or a foreign receipt is rejected before any read."""
    constructed = []

    class _Explode:
        def __init__(self, user_id: str) -> None:
            constructed.append(user_id)
            raise AssertionError("verification must not read")

    monkeypatch.setattr(bv, "_Reader", _Explode)

    async def _found(user_id: str, design_id: str):
        return SimpleNamespace(user_id=user_id), {
            "design_id": design_id,
            "blueprint": _blueprint(),
            "blueprint_revision": 2,
            "build_receipt": {
                "id": "xr.real",
                "design_id": design_id,
                "design_revision": 2,
            },
        }

    monkeypatch.setattr(bv, "_find_design", _found)
    revised = await verify_build("user", "d1", 1, "xr.real")
    assert revised["error"] == "revision_mismatch"
    mismatched = await verify_build("user", "d1", 2, "xr.other")
    assert mismatched["error"] == "receipt_mismatch"
    assert constructed == []


def test_verification_does_not_execute_a_build():
    """Verification has no apply path, and the same apply maps the same way."""
    source = inspect.getsource(bv)
    assert "bless_and_execute" not in source
    assert "commit_batch" not in source
    first = build_item_mapping(_blueprint(), _applied())
    second = build_item_mapping(_blueprint(), _applied())
    assert first == second


@pytest.mark.asyncio
async def test_reader_denies_when_the_caller_has_no_role(monkeypatch):
    """An object the caller cannot access is denied, even if the node exists."""
    from app.models.nodes import App

    async def _get(object_id: str):
        return SimpleNamespace(id=object_id, name="Bike Repair")

    async def _role(*_args: Any, **_kwargs: Any):
        return None

    monkeypatch.setattr(App, "get", _get)
    monkeypatch.setattr(bv, "resolve_role", _role)
    with pytest.raises(ReadDenied):
        await _Reader("user").read({"kind": "app", "object_id": "a1"})
