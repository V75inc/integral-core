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
    assert "registry_versions" in data
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
