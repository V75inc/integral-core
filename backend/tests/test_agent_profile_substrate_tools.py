"""Smoke tests for the manifest tool surface (Pillar 3 — agent contract)."""

import pytest

from app.agentive.staging_executors import _EXECUTORS, supports
from app.agentive.tooling.bindings import _stage_propose_profile_revision
from app.agentive.tooling.catalogue import build_tool_catalogue
from app.agentive.tooling.dispatch import dispatch_tool


def test_new_profile_tools_in_catalogue():
    expected = {
        "integral_describe_substrate",
        "integral_describe_model",
        "integral_get_model_draft",
        "integral_propose_model_revision",
        "integral_diff_model_draft",
        "integral_publish_model_draft",
        "integral_discard_model_draft",
    }
    names = {t["name"] for t in build_tool_catalogue()}
    assert expected.issubset(names)


def test_new_staging_kinds_registered():
    expected = {
        "propose_profile_revision",
        "apply_to_draft",
        "publish_profile_draft",
        "discard_profile_draft",
    }
    for kind in expected:
        assert supports(kind), f"missing executor for kind {kind!r}"
        assert kind in _EXECUTORS


def test_profile_revision_card_names_added_field_type_and_choices():
    staged = _stage_propose_profile_revision(
        {
            "draft_id": "n.OperationalModel.draft",
            "operations": [
                {
                    "op": "add_field",
                    "entry_type": "service_request",
                    "spec": {
                        "key": "priority",
                        "name": "Priority",
                        "type": "select",
                        "enum": ["Low", "Normal", "High"],
                    },
                }
            ],
        }
    )
    assert "Priority (`select`)" in staged["diff_human"]
    assert "Low, Normal, High" in staged["diff_human"]


@pytest.mark.asyncio
async def test_describe_substrate_returns_catalogue(test_user):
    if test_user is None:
        pytest.skip("no test_user node available")
    result = await dispatch_tool(
        "integral_describe_substrate",
        {},
        principal_id=test_user.user_id,
        scope=None,
    )
    assert result.is_error is False, result.message
    data = result.data
    assert "field_types" in data
    assert "view_types" in data
    assert "retrieval" in data
    from app.services.agent_profile_patches import supported_ops

    assert data["patch_operations"] == supported_ops()
    assert "set_default_view" not in data["patch_operations"]
    field_keys = {f["type"] for f in data["field_types"]}
    view_keys = {v["type"] for v in data["view_types"]}
    assert {"text", "number", "select"}.issubset(field_keys)
    assert {"feed", "kanban", "wiki", "composable_board"}.issubset(view_keys)


@pytest.mark.asyncio
async def test_describe_operational_model_requires_target(test_user):
    if test_user is None:
        pytest.skip("no test_user node available")
    result = await dispatch_tool(
        "integral_describe_model",
        {},
        principal_id=test_user.id,
        scope=None,
    )
    assert result.is_error is True
    assert result.error_code == "bad_request"


@pytest.mark.asyncio
async def test_propose_revision_rejects_empty_operations(test_user):
    # The propose surface STAGES (does not APPLY) — the draft is resolved only
    # on bless. The stager fail-closes on malformed input (empty operations
    # list), so a no-op revision is rejected before any StagedChange is minted.
    if test_user is None:
        pytest.skip("no test_user node available")
    result = await dispatch_tool(
        "integral_propose_model_revision",
        {
            "draft_id": "n.OperationalModel.does_not_exist",
            "operations": [],
        },
        principal_id=test_user.id,
        scope=None,
    )
    assert result.is_error is True
    assert "operations" in result.message


@pytest.mark.parametrize(
    "operation", [{"op": "set_default_view"}, {"op": "made_up"}, {}, "add_view", None]
)
def test_profile_revision_rejects_unknown_operations_before_staging(operation):
    from app.api.errors import BadRequestError

    with pytest.raises(BadRequestError):
        _stage_propose_profile_revision(
            {
                "draft_id": "n.OperationalModel.draft",
                "operations": [operation],
            }
        )


@pytest.mark.asyncio
async def test_existing_default_view_uses_reviewed_save_view(monkeypatch):
    from unittest.mock import AsyncMock

    from app.agentive.tooling import bindings
    from app.models.nodes import View

    monkeypatch.setattr(
        bindings,
        "get_scoped_view",
        AsyncMock(
            return_value=View(
                id="view-1",
                track_id="track-1",
                name="Member Profiles",
                type="table",
                config={"columns": ["title"]},
            )
        ),
    )
    monkeypatch.setattr(
        bindings._sd, "resolve_track_label", AsyncMock(return_value="Members")
    )
    config = {"columns": ["title"]}
    staged = await bindings._stage_save_view(
        {
            "track_id": "track-1",
            "view_id": "view-1",
            "name": "Member Profiles",
            "view_type": "table",
            "config": config,
            "is_default": True,
        }
    )
    assert staged["kind"] == "save_view"
    assert staged["payload"]["view_id"] == "view-1"
    assert staged["payload"]["config"]["columns"] == config["columns"]
    assert staged["payload"]["is_default"] is True
    assert "Opens the track by default" in staged["diff_human"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {"name": "Member Profiles", "view_type": "gallery"},
        {"name": "Renamed records"},
        {"view_type": "gallery"},
        {"config": {}},
    ],
)
async def test_default_selection_cannot_redefine_existing_view(monkeypatch, changed):
    from unittest.mock import AsyncMock

    from app.agentive.tooling import bindings
    from app.models.nodes import View

    target = View(
        id="records-id",
        track_id="members",
        name="Records",
        type="table",
        config={"columns": ["title"]},
    )
    monkeypatch.setattr(bindings, "get_scoped_view", AsyncMock(return_value=target))
    label = AsyncMock(return_value="Members")
    monkeypatch.setattr(bindings._sd, "resolve_track_label", label)
    args = dict(
        track_id="members",
        view_id=target.id,
        name=target.name,
        view_type=target.type,
        config=target.config,
        is_default=True,
    )
    args.update(changed)
    with pytest.raises(ValueError, match="must preserve"):
        await bindings._stage_save_view(args)
    label.assert_not_awaited()
    assert target.name == "Records"
    assert target.config == {"columns": ["title"]}


@pytest.mark.asyncio
async def test_explicit_layout_edit_remains_available_without_default_selection(
    monkeypatch,
):
    from unittest.mock import AsyncMock

    from app.agentive.tooling import bindings
    from app.models.nodes import View

    monkeypatch.setattr(
        bindings,
        "get_scoped_view",
        AsyncMock(
            return_value=View(
                id="records-id",
                track_id="members",
                name="Records",
                type="table",
                config={"columns": ["title"]},
            )
        ),
    )
    monkeypatch.setattr(
        bindings._sd, "resolve_track_label", AsyncMock(return_value="Members")
    )
    staged = await bindings._stage_save_view(
        dict(
            track_id="members",
            view_id="records-id",
            name="Renamed records",
            view_type="gallery",
            config={},
        )
    )
    assert staged["payload"]["name"] == "Renamed records"
    assert "is_default" not in staged["payload"]
