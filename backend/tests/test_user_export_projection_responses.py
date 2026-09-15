"""Response-level guards: member and watcher lists must not ship User internals.

The sibling ``test_workspace_members_user_leak.py`` asserts against the
*handler source* (that it calls ``public_user_view``, that it restores
``user_id``). That is a fast, precise guard on the shape of the fix, but it
proves nothing about what comes back — a future refactor could route the
projection through a helper that re-adds ``preferences`` and every source-level
assertion would stay green.

These tests seed the sensitive fields with recognisable values and then assert
on the **response dict the handler returns**. They would have caught the
original leak with no knowledge of how the handler is written.

Scope, stated precisely: they invoke the handlers through
``invoke_route_in_process`` (the pattern the workspace-member tests already
use), not over HTTP. That covers the projection itself — where the bug was —
but not serialization, so a middleware or encoder that re-attached fields
downstream would slip past. Nothing in this stack does that today; if one is
ever added, these move to ``authenticated_client``.

All three endpoints are readable by anyone with ``guest`` on the workspace or
read on the track/entry, so the leak's blast radius was "every member sees
every other member's OTP slot and phone number".
"""

from __future__ import annotations

import pytest

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
pytestmark = pytest.mark.smoke

# Anything on a graph User that is not in PUBLIC_USER_FIELDS. Asserted by name
# so the failure message says which field escaped, rather than a set diff.
FORBIDDEN_KEYS = (
    "preferences",
    "notification_preferences",
    "email_verified",
    "active_workspace_id",
    "onboarded_at",
    "password_hash",
)

# The email-verification OTP slot lives here (services/email_verification.py).
SEEDED_PREFERENCES = {
    "email_verification": {
        "hash": "sha256-of-the-live-otp",
        "expires_at": "2099-01-01T00:00:00Z",
        "attempts": 0,
    }
}
SEEDED_NOTIFICATION_PREFERENCES = {"sms": {"phone_e164": "+15555550123"}}


async def _make_user(email: str, name: str):
    """Auth principal + graph User node, with the sensitive fields populated."""
    from jvspatial.api.auth.models import UserCreate

    from app.api.auth import _get_auth_service
    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    resp = await _get_auth_service().register_user(
        UserCreate(email=email, password="testpassword123")
    )
    node = await User.create(
        user_id=resp.id,
        display_name=name,
        preferences=dict(SEEDED_PREFERENCES),
        notification_preferences=dict(SEEDED_NOTIFICATION_PREFERENCES),
    )
    await catalog_user(node)
    return resp.id, node


def _assert_clean(record: dict, *, where: str) -> None:
    for key in FORBIDDEN_KEYS:
        assert key not in record, (
            f"{where} response carries {key!r} — a raw User export reached the "
            f"response. Project through app.api.utils.public_user_view."
        )
    # Positive half: the projection must still be usable, or a future fix could
    # "pass" by returning {}.
    assert record.get("id"), f"{where} response lost the user id"
    assert record.get("display_name"), f"{where} response lost display_name"


@pytest.mark.asyncio
async def test_workspace_members_response_omits_user_internals():
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.workspaces import (
        add_workspace_member,
        create_workspace,
        list_workspace_members,
    )

    owner_auth, _owner = await _make_user("projection-owner@example.com", "Owner")
    _member_auth, member = await _make_user("projection-member@example.com", "Member")

    ws = await invoke_route_in_process(
        create_workspace,
        principal_id=owner_auth,
        name="Projection Org",
        workspace_type="collaborative",
    )
    ws_id = ws["workspace"]["id"]
    await invoke_route_in_process(
        add_workspace_member,
        principal_id=owner_auth,
        scope=ws_id,
        workspace_id=ws_id,
        member_user_id=member.id,
        role="member",
    )

    result = await invoke_route_in_process(
        list_workspace_members,
        principal_id=owner_auth,
        scope=ws_id,
        workspace_id=ws_id,
    )
    members = result["members"]
    seeded = [m for m in members if m.get("id") == member.id]
    assert seeded, "seeded member missing from the members list"

    for record in members:
        _assert_clean(record, where="GET /workspaces/{id}/members")

    # user_id is deliberately restored on top of the allowlist: the members page
    # resolves "which row is me" with `isSamePrincipal(me, m.user_id || m.id)`.
    # Dropping it degrades the UI silently, so it is pinned on the wire too.
    assert seeded[0].get("user_id"), (
        "members response lost user_id — the page can no longer identify the "
        "current user, and it fails without an error"
    )


async def _track_owned_by(principal_id: str, title: str) -> str:
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.tracks import create_track

    created = await invoke_route_in_process(
        create_track,
        principal_id=principal_id,
        title=title,
        visibility="private",
    )
    return created["track"]["id"]


@pytest.mark.asyncio
async def test_admin_create_user_response_omits_user_internals(
    authenticated_admin_client,
):
    """The admin create endpoint projects like every other user response.

    Never a live leak — the node is one statement old, so `preferences` holds
    only what the caller just sent and the OTP slot does not exist yet. It is
    pinned because "the admin endpoint is the one that returns a raw User" is
    the shape that becomes a leak as soon as someone widens what create
    accepts, or copies this handler as the pattern for a new one.

    Driven over HTTP (the `authenticated_admin_client` pattern from
    tests/test_users_admin_create.py) rather than through
    ``invoke_route_in_process``: platform-admin is a JWT ROLE, not a User node
    field. Going over the wire also covers serialization — the one gap the
    module docstring above names for the other tests here.
    """
    response = await authenticated_admin_client.post(
        "/api/users",
        json={
            "email": "projection-created@example.com",
            "display_name": "Created By Admin",
        },
    )
    assert response.status_code == 200, response.text

    created = response.json()["user"]
    for key in FORBIDDEN_KEYS:
        assert key not in created, (
            f"POST /api/users echoed {key!r} back to the admin — project "
            "through public_user_view like the list and get handlers do"
        )
    assert created.get("id"), "create response lost the user id"
    assert created.get("display_name") == "Created By Admin"


@pytest.mark.asyncio
async def test_track_watchers_response_omits_user_internals():
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.tracks import get_track_watchers
    from app.models.edges import WATCHES
    from app.models.nodes import Track

    owner_auth, _owner = await _make_user(
        "projection-watch-owner@example.com", "WOwner"
    )
    _other_auth, watcher = await _make_user("projection-watcher@example.com", "Watcher")

    track_id = await _track_owned_by(owner_auth, "Projection Track")

    # Wire the WATCHES edge directly rather than through /watch: this test is
    # about the shape of the read, and the write path has its own coverage in
    # test_track_watchers.py.
    track = await Track.get(track_id)
    await watcher.connect(track, edge=WATCHES)

    result = await invoke_route_in_process(
        get_track_watchers,
        principal_id=owner_auth,
        track_id=track_id,
    )
    watchers = result["watchers"]
    assert any(
        w.get("id") == watcher.id for w in watchers
    ), "seeded watcher missing from the watchers list"

    for record in watchers:
        _assert_clean(record, where="GET /tracks/{id}/watchers")


@pytest.mark.asyncio
async def test_entry_watchers_response_omits_user_internals():
    """The entry twin of the test above.

    `get_entry_watchers` is a separate handler with its own export call, so a
    fix applied to only one of the pair would leave this one leaking.
    """
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.entries import create_entry, get_entry_watchers
    from app.models.edges import WATCHES
    from app.models.nodes import Entry

    owner_auth, _owner = await _make_user(
        "projection-entry-owner@example.com", "EOwner"
    )
    _other_auth, watcher = await _make_user(
        "projection-entry-watcher@example.com", "EWatcher"
    )

    track_id = await _track_owned_by(owner_auth, "Projection Entry Track")
    created = await invoke_route_in_process(
        create_entry,
        principal_id=owner_auth,
        track_id=track_id,
        title="Watched Entry",
        body="body",
    )
    entry_id = created["entry"]["id"]

    entry = await Entry.get(entry_id)
    await watcher.connect(entry, edge=WATCHES)

    result = await invoke_route_in_process(
        get_entry_watchers,
        principal_id=owner_auth,
        entry_id=entry_id,
    )
    watchers = result["watchers"]
    assert any(
        w.get("id") == watcher.id for w in watchers
    ), "seeded watcher missing from the entry watchers list"

    for record in watchers:
        _assert_clean(record, where="GET /entries/{id}/watchers")
