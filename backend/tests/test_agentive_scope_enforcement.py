"""The agentive tool path must not accept an unvalidated workspace scope.

``POST /api/agentive/tools/{tool_name}`` takes ``scope`` from the request body
(or the ``X-Integral-Scope`` header) and hands it to ``dispatch_tool`` ->
``invoke_route_in_process``. That seeded ``request.state.workspace_resolution``
directly with the caller-supplied value, and
``resolve_workspace_id_from_request`` returns a memo hit *before* reaching its
``_user_member_of`` check -- so the membership gate never ran for any tool call.

That is not merely defense-in-depth. Handlers that treat the resolved workspace
as the gate, rather than re-checking each row, read straight through it. The
unfiltered ``GET /api/tags`` branch is exactly that shape (it scopes by looking
up the Apps and Tracks *in* the resolved workspace), and ``integral_list_tags``
is a manifest tool -- so a caller could name any workspace id and read its tags.

The fix is to stop pre-seeding the memo: the stub request already carries the
scope as a header, and the resolver validates the header against membership and
falls back to the caller's personal workspace when it does not hold.
"""

import pytest

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke


async def _bootstrap_user(email: str, display_name: str):
    """Create AuthUser + User node + personal workspace. Returns (auth_id, ws_id)."""
    from jvspatial.api.auth.models import UserCreate

    from app.api.auth import _get_auth_service
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.personal_workspace import ensure_personal_workspace

    auth_service = _get_auth_service()
    user_response = await auth_service.register_user(
        UserCreate(email=email, password="testpassword123")
    )
    auth_user_id = user_response.id
    user_node = await User.create(user_id=auth_user_id, display_name=display_name)
    await catalog_user(user_node)
    ws = await ensure_personal_workspace(user_node)
    return auth_user_id, (ws.id if ws else None)


@pytest.mark.asyncio
async def test_tool_call_cannot_name_another_users_workspace():
    """The exploit: victim's workspace id, attacker's principal.

    Without the fix the resolver returns the attacker-supplied workspace
    verbatim, and ``list_tags`` enumerates that workspace's tags.

    Since the SM3 fix the resolver no longer degrades to the caller's own
    Personal Workspace either — naming a workspace the principal cannot
    reach raises 403. Both outcomes are accepted here; the invariant under
    test is that the victim's tags are never returned.
    """
    from jvspatial.api.exceptions import InsufficientPermissionsError

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.tags import create_tag, list_tags
    from app.api.tracks import create_track

    victim_id, victim_ws = await _bootstrap_user("scope-victim@example.com", "Victim")
    attacker_id, attacker_ws = await _bootstrap_user(
        "scope-attacker@example.com", "Attacker"
    )
    assert victim_ws and attacker_ws and victim_ws != attacker_ws

    track = await invoke_route_in_process(
        create_track,
        principal_id=victim_id,
        scope=victim_ws,
        title="Victim Private Track",
        visibility="private",
    )
    track_id = track["track"]["id"]
    await invoke_route_in_process(
        create_tag,
        principal_id=victim_id,
        scope=victim_ws,
        name="victim-secret-tag",
        track_id=track_id,
    )

    # Attacker names the victim's workspace. This is the body `scope` that
    # execute_tool_endpoint forwards verbatim.
    try:
        leaked = await invoke_route_in_process(
            list_tags,
            principal_id=attacker_id,
            scope=victim_ws,
        )
    except InsufficientPermissionsError:
        return  # Hard refusal — strictly stronger than an empty result.

    names = {(t.get("name") or "") for t in (leaked or {}).get("tags") or []}
    assert "victim-secret-tag" not in names, (
        "agentive tool call read another workspace's tags by naming its id: "
        f"{sorted(names)}"
    )


@pytest.mark.asyncio
async def test_tool_call_still_works_within_the_callers_own_workspace():
    """Enforcement must not break the legitimate path.

    This is the failure mode the fix could plausibly introduce -- scoping so
    hard that an agent can no longer read its own workspace.
    """
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.tags import create_tag, list_tags
    from app.api.tracks import create_track

    user_id, ws_id = await _bootstrap_user("scope-owner@example.com", "Owner")
    track = await invoke_route_in_process(
        create_track,
        principal_id=user_id,
        scope=ws_id,
        title="Owner Track",
        visibility="private",
    )
    await invoke_route_in_process(
        create_tag,
        principal_id=user_id,
        scope=ws_id,
        name="owner-visible-tag",
        track_id=track["track"]["id"],
    )

    result = await invoke_route_in_process(list_tags, principal_id=user_id, scope=ws_id)
    names = {(t.get("name") or "") for t in (result or {}).get("tags") or []}
    assert "owner-visible-tag" in names


@pytest.mark.asyncio
async def test_memo_is_not_pre_seeded_with_unvalidated_scope():
    """Guards the mechanism, so the optimization cannot quietly return.

    Re-seeding ``workspace_resolution`` before validation would restore the
    bypass even if the behavioural tests above were later reshaped.
    """
    import inspect
    import re

    from app.agentive.tooling import invoke

    src = inspect.getsource(invoke.invoke_route_in_process)
    # Strip comments and docstring prose: this file *documents* the memo at
    # length, so a bare substring match would flag its own explanation.
    code_only = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    writes = re.findall(r"workspace_resolution\s*(?:=|\[)", code_only)
    assert not writes, (
        "invoke_route_in_process assigns to the scope memo again; "
        "resolve_workspace_id_from_request returns memo hits before its "
        "membership check, so this reintroduces the cross-workspace bypass"
    )
