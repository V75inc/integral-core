"""Personal Context is provisioned with the personal workspace, exactly once.

The App has to exist before the user's first turn — that turn is the first
thing worth observing — so it is installed alongside the personal workspace
at signup rather than lazily at first agent connect the way ``agent-scratch``
is.

What is defended here:

- signup leaves the App installed and active, with the seven tracks the
  manifest prescribes;
- calling provisioning again returns the SAME App, both from the per-process
  cache and, with the cache cleared, from the graph — a retry, a second
  signup call and a concurrent first-read all converge on one App;
- the App and its tracks are reachable from Root (I-GRAPH-01) — install_app
  wires them, and this asserts the wiring rather than trusting it;
- provisioning is non-fatal: when the library package is missing it returns
  None and logs, because signup must not fail over an App install.

Marked ``library`` so the disk bundle catalog is seeded — the whole point is
that the real ``personal-context`` manifest installs.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models.edges import CATALOGS, CONTAINS, IS_MEMBER_OF, OWNS
from app.models.nodes import App, Workspace
from app.services import personal_context as pc
from app.services.personal_context import (
    PERSONAL_CONTEXT_SLUG,
    provision_personal_context_app,
)

pytestmark = pytest.mark.library

EXPECTED_TRACKS = {
    "Stream",
    "Identity",
    "Arenas",
    "People",
    "Commitments",
    "Pages",
    "Attention Log",
}


@pytest.fixture(autouse=True)
def _reset_cache():
    pc.reset_personal_context_cache()
    yield
    pc.reset_personal_context_cache()


@pytest.mark.asyncio
async def test_provisions_the_app_in_the_personal_workspace(test_user):
    app_node = await provision_personal_context_app(user_id=test_user.id)

    assert app_node is not None, "provisioning returned None"
    assert app_node.source_profile_slug == PERSONAL_CONTEXT_SLUG
    assert app_node.lifecycle_state == "active"

    workspace = await Workspace.get(app_node.workspace_id)
    assert workspace is not None
    assert workspace.kind == "personal", "the App must not land in an org workspace"


@pytest.mark.asyncio
async def test_settings_take_the_manifest_defaults(test_user):
    # install_app pauses at awaiting_settings when a manifest declares a
    # settings_schema and the caller supplies nothing. Provisioning passes
    # {} precisely so the defaults apply — there is nobody to answer a
    # settings prompt at signup.
    app_node = await provision_personal_context_app(user_id=test_user.id)
    assert app_node is not None
    settings = dict(app_node.settings or {})
    assert settings.get("attention_enabled") is True
    assert settings.get("promotion_policy") == "review_daily"
    assert settings.get("question_budget") == 1
    assert settings.get("excluded_arenas") == []


@pytest.mark.asyncio
async def test_prescribed_tracks_are_materialized(test_user):
    app_node = await provision_personal_context_app(user_id=test_user.id)
    assert app_node is not None
    tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"], direction="out")
    titles = {getattr(t, "title", "") for t in tracks}
    missing = EXPECTED_TRACKS - titles
    assert not missing, f"tracks not provisioned: {sorted(missing)}"


@pytest.mark.asyncio
async def test_second_call_is_a_no_op(test_user):
    first = await provision_personal_context_app(user_id=test_user.id)
    second = await provision_personal_context_app(user_id=test_user.id)
    assert first is not None and second is not None
    assert first.id == second.id


@pytest.mark.asyncio
async def test_existing_install_short_circuits_before_install_app(test_user):
    # install_app is idempotent on its own, but the reuse branch still
    # re-materializes tracks and re-plants seeds — real work on a path that
    # runs at every signup. The graph lookup exists to skip it, so pin that
    # it actually does: with the cache cleared, install_app must not be
    # reached at all.
    first = await provision_personal_context_app(user_id=test_user.id)
    assert first is not None
    pc.reset_personal_context_cache()

    with patch.object(pc, "install_app", side_effect=AssertionError) as spy:
        again = await provision_personal_context_app(user_id=test_user.id)
    assert spy.call_count == 0
    assert again is not None and again.id == first.id


@pytest.mark.asyncio
async def test_idempotent_without_the_cache(test_user):
    # With the per-process cache cleared — a fresh worker, a restarted
    # process — provisioning still converges on the one App rather than
    # minting a second.
    first = await provision_personal_context_app(user_id=test_user.id)
    assert first is not None
    pc.reset_personal_context_cache()
    second = await provision_personal_context_app(user_id=test_user.id)
    assert second is not None
    assert second.id == first.id

    installed = [
        a
        for a in (
            await App.find({"context.source_profile_slug": PERSONAL_CONTEXT_SLUG})
        )
        or []
        if getattr(a, "workspace_id", "") == first.workspace_id
    ]
    assert len(installed) == 1, f"expected one install, found {len(installed)}"


@pytest.mark.asyncio
async def test_app_and_tracks_are_reachable_from_root(test_user):
    # I-GRAPH-01. install_app owns the wiring; this asserts it rather than
    # assuming it, because a detached App is invisible to walkers, cascade
    # delete and backup.
    app_node = await provision_personal_context_app(user_id=test_user.id)
    assert app_node is not None

    from app.services.app_graph import _resolve_workspace_branch

    branch = await _resolve_workspace_branch(app_node.workspace_id, "apps")
    assert branch is not None, "workspace has no apps branch registry"
    # The graph entity name is "WorkspaceApp" (App.__entity_name__) — the
    # class was renamed forward, the discriminator was not.
    cataloged = await branch.nodes(
        edge=[CATALOGS], node=["WorkspaceApp"], direction="out"
    )
    assert app_node.id in {
        a.id for a in cataloged
    }, "App not cataloged under the workspace's apps branch"

    owners = await app_node.nodes(edge=[OWNS], node=["User"], direction="in")
    assert owners, "App has no OWNS edge — nobody could administer it"

    tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"], direction="out")
    assert tracks, "App has no tracks"
    for track in tracks:
        parents = await track.nodes(
            edge=[CONTAINS], node=["WorkspaceApp"], direction="in"
        )
        assert app_node.id in {
            p.id for p in parents
        }, f"track {track.title!r} has no App parent"


@pytest.mark.asyncio
async def test_missing_library_package_is_not_fatal(test_user):
    # Signup must survive an unseeded catalog: the user simply has no
    # Personal Context yet, and the next call provisions it.
    with patch.object(pc, "_resolve_library_package", return_value=None):
        assert await provision_personal_context_app(user_id=test_user.id) is None


@pytest.mark.asyncio
async def test_unknown_user_is_not_fatal():
    assert await provision_personal_context_app(user_id="n.User.nope") is None


@pytest.mark.asyncio
async def test_signup_provisions_it(client):
    # The wiring, end to end through the real endpoint.
    email = "pc-signup@example.com"
    resp = await client.post(
        "/api/auth/signup",
        json={"email": email, "password": "testpassword123", "name": "PC Signup"},
    )
    assert resp.status_code in (200, 201), resp.text

    from app.models.nodes import User

    users = await User.find({"context.display_name": "PC Signup"}) or []
    assert users, "signup did not create a User node"
    user_id = users[0].id

    user = users[0]
    workspaces = await user.nodes(edge=[IS_MEMBER_OF], node=["Workspace"])
    personal = [w for w in workspaces if getattr(w, "kind", "") == "personal"]
    assert personal, "signup did not create a personal workspace"

    installed = [
        a
        for a in (
            await App.find({"context.source_profile_slug": PERSONAL_CONTEXT_SLUG})
        )
        or []
        if getattr(a, "workspace_id", "") == personal[0].id
    ]
    assert installed, "signup did not provision Personal Context"
    assert installed[0].lifecycle_state == "active"
    assert user_id  # the User node resolved above is the one we walked from


@pytest.mark.asyncio
async def test_track_kinds_are_not_used_as_the_discriminator(test_user):
    # The install is identified by App.source_profile_slug, the value
    # install_app stamps from package.slug — never by a title a user can
    # rename. Renaming the App must not orphan it.
    app_node = await provision_personal_context_app(user_id=test_user.id)
    assert app_node is not None
    app_node.name = "My Brain"
    await app_node.save()
    pc.reset_personal_context_cache()
    again = await provision_personal_context_app(user_id=test_user.id)
    assert again is not None and again.id == app_node.id
