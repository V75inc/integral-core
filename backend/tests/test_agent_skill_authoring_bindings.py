"""Tool-binding stagers for the agent skill-authoring tools."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agentive.tooling import bindings
from app.exceptions import InvalidToolReferenceError


@pytest.mark.asyncio
async def test_stage_author_skill_shape():
    staged = await bindings._stage_author_skill(
        {
            "key": "my_skill",
            "name": "My Skill",
            "description": "Does a thing.",
            "body_override": "# My Skill",
        }
    )
    assert staged["kind"] == "author_skill"
    assert staged["payload"]["key"] == "my_skill"
    assert staged["payload"]["tools_required"] == []


@pytest.mark.asyncio
async def test_stage_author_skill_requires_fields():
    with pytest.raises(ValueError):
        await bindings._stage_author_skill({"key": "my_skill"})


@pytest.mark.asyncio
async def test_stage_author_skill_key_is_optional():
    """The agent often supplies name but skips key — it must not be required
    (observed regression: 'missing required argument(s): key')."""
    staged = await bindings._stage_author_skill(
        {
            "name": "Daily Standup Reminder",
            "description": "Nudges the team.",
            "body_override": "# Daily Standup Reminder",
        }
    )
    assert staged["kind"] == "author_skill"
    assert "key" not in staged["payload"]


@pytest.mark.asyncio
async def test_stage_author_skill_rejects_unknown_tools_at_stage_time():
    """Observed regression: the agent passed SKILL names ('integral_entries',
    'integral_identity') where TOOL names belong. Validation must fail here,
    at stage time — a clean recoverable tool-call error the agent can retry
    this same turn — not surface as a raw traceback after the user already
    approved the card (validate_tools_required previously only ran inside
    create_workspace_skill, at bless/execute time)."""
    with pytest.raises(InvalidToolReferenceError):
        await bindings._stage_author_skill(
            {
                "name": "My Skill",
                "description": "Does a thing.",
                "body_override": "# My Skill",
                "tools_required": ["integral_entries", "integral_identity"],
            }
        )


@pytest.mark.asyncio
async def test_stage_update_skill_shape():
    staged = await bindings._stage_update_skill(
        {"skill_id": "n.Skill.abc", "name": "New Name"}
    )
    assert staged["kind"] == "update_skill"
    assert staged["payload"] == {"skill_id": "n.Skill.abc", "name": "New Name"}


@pytest.mark.asyncio
async def test_stage_update_skill_rejects_unknown_tools_at_stage_time():
    with pytest.raises(InvalidToolReferenceError):
        await bindings._stage_update_skill(
            {"skill_id": "n.Skill.abc", "tools_required": ["not_a_real_tool"]}
        )


def test_stage_delete_skill_shape():
    staged = bindings._stage_delete_skill({"skill_id": "n.Skill.abc"})
    assert staged["kind"] == "delete_skill"
    assert staged["payload"] == {"skill_id": "n.Skill.abc"}


def test_tools_bound_and_dispatchable():
    for name, stager in (
        ("integral_author_skill", bindings._stage_author_skill),
        ("integral_update_skill", bindings._stage_update_skill),
        ("integral_delete_skill", bindings._stage_delete_skill),
    ):
        binding = bindings.TOOL_BINDINGS.get(name)
        assert binding is not None, f"{name} missing from TOOL_BINDINGS"
        assert binding.stager is stager


@pytest.mark.asyncio
async def test_stage_author_skill_names_the_app_when_app_id_given(monkeypatch):
    monkeypatch.setattr(
        bindings._sd,
        "load_app_record",
        AsyncMock(return_value={"id": "n.App.xyz", "name": "Vendor Contracts"}),
    )

    staged = await bindings._stage_author_skill(
        {
            "name": "Vendor SOP",
            "description": "How to file vendor contracts.",
            "body_override": "# SOP",
            "app_id": "n.App.xyz",
        }
    )
    assert staged["payload"]["app_id"] == "n.App.xyz"
    assert "Vendor Contracts" in staged["diff_human"]


@pytest.mark.asyncio
async def test_stage_author_skill_omits_app_id_when_not_given():
    staged = await bindings._stage_author_skill(
        {
            "name": "Plain Skill",
            "description": "No app scoping.",
            "body_override": "# SOP",
        }
    )
    assert "app_id" not in staged["payload"]


@pytest.mark.asyncio
async def test_stage_update_skill_forwards_private():
    staged = await bindings._stage_update_skill(
        {"skill_id": "n.Skill.abc", "private": False}
    )
    assert staged["payload"]["private"] is False
