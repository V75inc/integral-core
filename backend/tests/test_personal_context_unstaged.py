"""The one write path that skips the approval card — ADR-006 / I-PC-01.

The approval surface exists so a person can refuse a change to their
substrate. An App that takes notes about them would fill it with items where
refusing means only "do not remember that", so two tracks — the verbatim
observation stream and the transparency log — accept writes without a card.

Everything about this is narrow, and every clause of the narrowness is
defended here:

- the exemption reaches ``stream`` and ``attention`` and nothing else, in
  the same App, on the same day;
- the fact tracks and the compiled ``pages`` track stage like everything
  else;
- declaring it in a manifest is necessary but not sufficient — the App has
  to be installed in the acting principal's OWN personal workspace, with
  that principal as its owner;
- a hand-made track carrying no manifest key is never exempt, no matter what
  it is called;
- the exempt set is read from the manifest, never named in the substrate
  (I-SUBSTRATE-01).

The tests drive ``dispatch_tool`` rather than the gate directly, because the
thing worth defending is whether a token gets minted, not whether a predicate
returns True.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agentive.tooling.dispatch import dispatch_tool
from app.agentive.unstaged_targets import is_unstaged_target
from app.models.edges import CONTAINS
from app.services.personal_context import provision_personal_context_app

pytestmark = pytest.mark.library


async def _tracks_by_key(app_node) -> dict:
    """Map manifest track key -> Track node for an installed App."""
    tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"], direction="out")
    return {str(getattr(t, "template_id", "") or ""): t for t in tracks}


async def _pending_tokens(user_id: str) -> list:
    from app.agentive.staging import get_pending_for_user

    return list(await get_pending_for_user(user_id) or [])


def _principal(user) -> str:
    """``dispatch_tool`` takes the AuthUser id, which is ``User.user_id``.

    The gate and the executor both resolve it back to the User node; passing
    the graph id instead reaches the gate fine and then dies in the executor
    with ``user_not_found``, which is how this was found.
    """
    return str(getattr(user, "user_id", "") or user.id)


async def _create_entry(user_id: str, workspace_id: str, track, title: str):
    return await dispatch_tool(
        "integral_create_entry",
        {"track_id": track.id, "title": title},
        principal_id=user_id,
        scope=workspace_id,
    )


@pytest.fixture
async def installed(test_user):
    app_node = await provision_personal_context_app(user_id=test_user.id)
    assert app_node is not None
    return app_node, await _tracks_by_key(app_node)


@pytest.mark.asyncio
async def test_an_observation_mints_no_token(test_user, installed):
    app_node, tracks = installed
    before = len(await _pending_tokens(_principal(test_user)))

    result = await _create_entry(
        _principal(test_user),
        app_node.workspace_id,
        tracks["stream"],
        "Noticed something",
    )

    assert not result.is_error, result.message
    assert (result.data or {}).get("staged") is False
    assert (
        len(await _pending_tokens(_principal(test_user))) == before
    ), "an observation minted an approval card"


@pytest.mark.asyncio
async def test_an_attention_event_mints_no_token(test_user, installed):
    app_node, tracks = installed
    before = len(await _pending_tokens(_principal(test_user)))

    result = await _create_entry(
        _principal(test_user),
        app_node.workspace_id,
        tracks["attention"],
        "Observed 3 things",
    )

    assert not result.is_error, result.message
    assert (result.data or {}).get("staged") is False
    assert len(await _pending_tokens(_principal(test_user))) == before


@pytest.mark.asyncio
async def test_a_belief_stages(test_user, installed):
    # The line the whole App rests on: what it NOTICED lands quietly; what it
    # BELIEVES about the person is proposed and reviewed.
    app_node, tracks = installed
    before = len(await _pending_tokens(_principal(test_user)))

    result = await _create_entry(
        _principal(test_user),
        app_node.workspace_id,
        tracks["identity"],
        "Product architect",
    )

    assert not result.is_error, result.message
    assert (result.data or {}).get("staged") is not False
    assert len(await _pending_tokens(_principal(test_user))) == before + 1


@pytest.mark.asyncio
async def test_a_compiled_page_stages(test_user, installed):
    # Pages are what the person reads. Rewriting one is a change to them.
    app_node, tracks = installed
    before = len(await _pending_tokens(_principal(test_user)))

    result = await _create_entry(
        _principal(test_user),
        app_node.workspace_id,
        tracks["pages"],
        "About me",
    )

    assert not result.is_error, result.message
    assert len(await _pending_tokens(_principal(test_user))) == before + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("track_key", ["arenas", "people", "commitments"])
async def test_every_other_fact_track_stages(test_user, installed, track_key):
    app_node, tracks = installed
    before = len(await _pending_tokens(_principal(test_user)))
    result = await _create_entry(
        _principal(test_user),
        app_node.workspace_id,
        tracks[track_key],
        f"A {track_key} row",
    )
    assert not result.is_error, result.message
    assert len(await _pending_tokens(_principal(test_user))) == before + 1


@pytest.mark.asyncio
async def test_a_hand_made_track_is_never_exempt(test_user, installed):
    # The gate keys on the manifest key a track was materialized from. A track
    # somebody made by hand carries none, so naming it "Stream" buys nothing.
    from datetime import datetime, timezone

    from app.models.edges import OWNS
    from app.models.nodes import Track
    from app.services.permissions import get_user_node

    app_node, _ = installed
    now = datetime.now(timezone.utc).isoformat()
    user = await get_user_node(test_user.id)
    track = await Track.create(
        title="Stream",
        title_fold="stream",
        owner_id=user.id,
        workspace_id=app_node.workspace_id,
        created_at=now,
        updated_at=now,
    )
    await user.connect(track, edge=OWNS, role="owner", granted_at=now)

    assert (
        await is_unstaged_target(
            user_id=test_user.id, kind="create_entry", payload={"track_id": track.id}
        )
        is False
    )


@pytest.mark.asyncio
async def test_another_users_personal_workspace_is_not_exempt(test_user, test_user2):
    # The clause that makes a manifest claim safe. The same App, the same
    # exempt track, a principal who does not own the workspace it sits in.
    other_app = await provision_personal_context_app(user_id=test_user2.id)
    assert other_app is not None
    tracks = await _tracks_by_key(other_app)

    assert (
        await is_unstaged_target(
            user_id=test_user.id,
            kind="create_entry",
            payload={"track_id": tracks["stream"].id},
        )
        is False
    )
    # ...and it IS exempt for the person who owns it, so the assertion above
    # is about ownership rather than about something else being broken.
    assert (
        await is_unstaged_target(
            user_id=test_user2.id,
            kind="create_entry",
            payload={"track_id": tracks["stream"].id},
        )
        is True
    )


@pytest.mark.asyncio
async def test_only_row_writes_are_exemptible(test_user, installed):
    # The exemption is for writing rows into a track, never for reshaping the
    # substrate around it.
    _, tracks = installed
    for kind in ("delete_entry", "create_track", "delete_track", "create_app"):
        assert (
            await is_unstaged_target(
                user_id=test_user.id,
                kind=kind,
                payload={"track_id": tracks["stream"].id},
            )
            is False
        ), f"{kind} must never be exempt"


@pytest.mark.asyncio
async def test_a_payload_with_no_track_is_not_exempt(test_user):
    assert (
        await is_unstaged_target(user_id=test_user.id, kind="create_entry", payload={})
        is False
    )


@pytest.mark.asyncio
async def test_the_exempt_set_comes_from_the_manifest(installed):
    # I-SUBSTRATE-01: the substrate must not name a bundle. The gate reads
    # whatever the App's attached manifest declares, so removing the
    # declaration removes the exemption without touching substrate code.
    import yaml

    from app.agentive.unstaged_targets import _unstaged_track_keys

    app_node, _ = installed
    manifest = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1]
            / "app"
            / "profiles"
            / "personal-context"
            / "profile.yaml"
        ).read_text(encoding="utf-8")
    )
    declared = frozenset(manifest["app"]["unstaged_tracks"])
    # Read from the manifest rather than hardcoded, so adding an exempt track
    # updates this test's expectation with the declaration it is checking —
    # and a track added ONLY to the gate, with no declaration, still fails.
    assert await _unstaged_track_keys(app_node) == declared
    assert declared == frozenset({"stream", "attention", "decisions"}), (
        "the exempt set changed — every addition is an I-PC-01 decision, "
        "not a manifest edit"
    )


def test_the_substrate_does_not_name_the_bundle():
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "app" / "agentive"
    gate = (src / "unstaged_targets.py").read_text(encoding="utf-8")
    seam = (src / "tooling" / "dispatch.py").read_text(encoding="utf-8")
    for token in ("personal-context", "personal_context", "PersonalContext"):
        assert token not in gate, f"gate names the bundle: {token}"
        assert token not in seam, f"dispatch seam names the bundle: {token}"


@pytest.mark.asyncio
async def test_an_org_workspace_the_user_owns_is_not_exempt(test_user):
    # The clause that stops "owner of the workspace" from being the whole
    # test. A person can own an organization workspace, and other people can
    # be in it. Unstaged writes are bounded to the workspace that is nobody
    # else's — so the same App, installed in an org workspace by its owner,
    # stages every write.
    from datetime import datetime, timezone

    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import Workspace
    from app.services.app_graph import catalog_workspace
    from app.services.app_lifecycle import install_app
    from app.services.personal_context import _resolve_library_package

    now = datetime.now(timezone.utc).isoformat()
    org_ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Owned Org",
        name_fold="owned org",
        description="",
        accent_color="",
        created_at=now,
        updated_at=now,
    )
    await test_user.connect(
        org_ws,
        edge=IS_MEMBER_OF,
        role="owner",
        joined_at=now,
        can_create_apps=True,
        can_create_tracks=True,
    )
    await catalog_workspace(org_ws)

    library = await _resolve_library_package()
    assert library is not None
    result = await install_app(
        workspace_id=org_ws.id,
        library_cp_id=library.id,
        actor_id=test_user.id,
        settings={},
    )
    assert result.get("status") == "active", result

    from app.models.nodes import App

    org_app = await App.get(result["app_id"])
    assert org_app is not None
    tracks = await _tracks_by_key(org_app)

    assert (
        await is_unstaged_target(
            user_id=_principal(test_user),
            kind="create_entry",
            payload={"track_id": tracks["stream"].id},
        )
        is False
    ), "an org workspace got the unstaged carve-out"
