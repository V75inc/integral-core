"""W1.6 — authored skills carry the design's explicit visibility choice."""

from __future__ import annotations

import pytest

from tests.test_build_anchors import _PROPOSAL, _approved_thread, _blueprint, _plan

pytestmark = pytest.mark.smoke

_BODY = """## When to use
Hand a finished project to the client.
## When NOT to use
Projects still in progress.
## Grounding
Read the project with integral_query_entries.
## Procedure
Stage the delivered status with integral_update_entry.
## Staging discipline
Wait for approval, then verify.
## Forbidden patterns
Never invent ids.
## Example
Hand off Website.
"""


def _skills(handoff_visibility: str) -> list:
    return [
        {
            "id": "skill.handoff",
            "name": "Client handoff",
            "purpose": "Close out a delivered project",
            "visibility": handoff_visibility,
        },
        {
            "id": "skill.kickoff",
            "name": "Project kickoff",
            "purpose": "Open a new project",
        },
    ]


def _skill_op(name: str, **extra) -> dict:
    return {
        "tool": "integral_author_skill",
        "args": {
            "name": name,
            "description": f"{name} for studio projects.",
            "tools_required": ["integral_query_entries", "integral_update_entry"],
            "body_override": _BODY,
            **extra,
        },
    }


async def _say(session_id: str, content: str) -> None:
    from app.models.edges import CONTAINS
    from app.models.nodes import ChatMessage, ChatThread

    thread = next(
        t for t in await ChatThread.find({}) if t.provider_session_id == session_id
    )
    msg = await ChatMessage.create(
        role="user", thread_id=thread.id, parts=[{"type": "text", "text": content}]
    )
    await thread.connect(msg, edge=CONTAINS)


async def _propose(call, skills):
    blueprint = _blueprint()
    blueprint["skills"] = skills
    return await call(
        "integral_propose_design",
        summary="Studio",
        proposal=_PROPOSAL
        + " Skills: Client handoff, Project kickoff."
        + " Seeds: Website (Acme), Brand refresh (Globex).",
        blueprint=blueprint,
    )


@pytest.mark.asyncio
async def test_build_scopes_each_skill_by_its_approved_visibility(
    authenticated_client, test_user
) -> None:
    from app.agentive import staging
    from app.models.nodes import App, Skill

    ws, call, approve = await _approved_thread(
        authenticated_client, test_user, "Vis build"
    )
    await _say("anchor-Vis build", "handoff should work everywhere in the workspace")
    proposed = await _propose(call, _skills("workspace"))
    assert not proposed.is_error, proposed
    await approve()

    # The planner gets scope wrong both ways; the approved design wins.
    plan = _plan() + [
        _skill_op("Client handoff", private=True),
        _skill_op("Project kickoff", private=False),
    ]
    built = await call("integral_build_approved_design", operations=plan)
    assert not built.is_error, built
    assert built.data["applied"] is True, built.data
    staging._reset_for_tests()

    app = next(
        a for a in await App.find({"context.name": "Studio"}) if a.workspace_id == ws
    )
    skills = {s.name: s for s in await Skill.find({"workspace_id": ws})}
    assert {n: (s.app_id, s.private) for n, s in skills.items()} == {
        "Client handoff": (app.id, False),
        "Project kickoff": (app.id, True),
    }


@pytest.mark.asyncio
async def test_blueprint_shaped_skill_op_gets_an_actionable_refusal(
    authenticated_client, test_user
) -> None:
    _, call, approve = await _approved_thread(
        authenticated_client, test_user, "Vis refusal"
    )
    proposed = await _propose(call, _skills("app_private"))
    assert not proposed.is_error, proposed
    await approve()

    echoed = {
        "tool": "integral_author_skill",
        "args": {"name": "Client handoff", "purpose": "Close out a project"},
    }
    built = await call(
        "integral_build_approved_design",
        operations=_plan() + [echoed, _skill_op("Project kickoff")],
    )
    assert built.is_error
    assert "missing required argument(s): body_override" in (built.message), built
