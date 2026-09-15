"""Refusing a workspace-targeting arg out loud instead of dropping it.

Workspace scope is bound from the conversation and no tool argument may
retarget it (tool_manifest.yaml § conventions.scope, PC-2). That is enforced —
but for most tools it was enforced by ``_pick`` quietly discarding the arg, so
the tool answered for the BOUND workspace and the model read that as its filter
having been honoured.

Observed live: asked to stage a track in an app belonging to another workspace,
the resident called ``integral_list_apps`` with that workspace, got this
workspace's apps back, concluded the API was ignoring the filter, looped
through eight tool calls and gave up mid-task by dumping raw JSON at the user.

The rule is right; the silence was the bug.
"""

import pytest

from app.agentive.tooling.bindings import TOOL_BINDINGS, _pick
from app.agentive.tooling.dispatch import _binding_drops, dispatch_tool

SCOPE = "n.Workspace.bound"
OTHER = "n.Workspace.elsewhere"


@pytest.mark.asyncio
async def test_refuses_a_workspace_arg_the_tool_would_have_discarded():
    res = await dispatch_tool(
        "integral_list_apps",
        {"workspace_id": OTHER},
        principal_id="u_1",
        scope=SCOPE,
    )

    assert res.is_error
    assert res.error_code == "scope_violation"


@pytest.mark.asyncio
async def test_the_refusal_tells_the_model_what_to_do_instead():
    # A refusal the model cannot act on is why it retried and then gave up.
    res = await dispatch_tool(
        "integral_list_apps",
        {"workspace_id": OTHER},
        principal_id="u_1",
        scope=SCOPE,
    )

    msg = res.message or ""
    assert SCOPE in msg, "must name the workspace actually in effect"
    assert "switch" in msg.lower(), "must point at the way out"
    assert "not retry" in msg.lower(), "must stop the retry loop it caused"


@pytest.mark.asyncio
async def test_the_bound_workspace_is_not_a_violation():
    # Naming the workspace you are already in is redundant, not wrong.
    res = await dispatch_tool(
        "integral_list_apps",
        {"workspace_id": SCOPE},
        principal_id="u_1",
        scope=SCOPE,
    )

    assert res.error_code != "scope_violation"


def test_tools_that_accept_a_workspace_are_left_alone():
    # `integral_workspace_setup` is documented to take an optional workspace_id
    # validated against the caller's membership (tool_manifest.yaml
    # § H_structure). Refusing it would break sanctioned behaviour — the guard
    # fires only where the arg is provably discarded, which is the case for
    # every plain reader.
    assert not _binding_drops(TOOL_BINDINGS["integral_workspace_setup"], "workspace_id")
    assert _binding_drops(TOOL_BINDINGS["integral_list_apps"], "workspace_id")


def test_a_bespoke_mapper_is_never_assumed_to_drop():
    # Only `_pick` advertises its keys. A hand-written mapper might forward the
    # arg, so it must read as "cannot prove" rather than "drops" — the guard
    # stays conservative and never refuses a tool that might honour it.
    from app.agentive.tooling.bindings import ToolBinding

    bespoke = ToolBinding(service_param_map=lambda a: dict(a or {}))
    assert not _binding_drops(bespoke, "workspace_id")

    declared = ToolBinding(service_param_map=_pick("limit"))
    assert _binding_drops(declared, "workspace_id")


@pytest.mark.asyncio
async def test_workspace_attachments_reads_only_the_bound_workspace():
    """The one attachment reader that took a workspace by argument.

    It gated on membership alone, so a conversation scoped to workspace A could
    list every file in workspace B the caller happened to belong to — legal by
    membership, and exactly the cross-workspace read PC-2 refuses. The binding
    no longer forwards the arg, so a mismatched one is refused rather than
    honoured.
    """
    res = await dispatch_tool(
        "integral_list_workspace_attachments",
        {"workspace_id": OTHER},
        principal_id="u_1",
        scope=SCOPE,
    )

    assert res.is_error
    assert res.error_code == "scope_violation"


@pytest.mark.asyncio
async def test_the_service_refuses_a_mismatch_however_it_is_reached():
    """Defence in depth: the binding is not the only way into the service."""
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.attachment_agent import list_attachments_for_workspace

    token = current_scope_workspace_id.set(SCOPE)
    try:
        with pytest.raises(PermissionError, match="scoped to another workspace"):
            await list_attachments_for_workspace(user_id="u_1", workspace_id=OTHER)
    finally:
        current_scope_workspace_id.reset(token)
