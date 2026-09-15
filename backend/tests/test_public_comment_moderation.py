"""Who may delete a comment — and specifically, who may delete a visitor's.

``delete_comment`` was author-only::

    if comment.author_id != user_id:
        raise InsufficientPermissionsError(...)

The public-share endpoint stamps a literal ``author_id="public"``
(``tracks_public_share.create_public_track_entry_comment``, ``auth=False``).
That string is not a principal id and never will be, so the two together made
anonymous comments **permanently undeletable by anybody**: turning on "Add
Comments" for a public link was a one-way door, and the only guard on what
landed there was the profanity filter. Found by driving the live app as an
unauthenticated visitor on 2026-08-12; the owner got 403 twice and the rows had
to be removed directly from Postgres.

The fix routes a non-author deleter through ``comment.moderate``, which
policy_engine puts at the **admin** tier — deliberately not the commenter tier
the other ``comment.*`` actions ride, since this authorizes deleting someone
else's words. ``test_a_track_commenter_cannot_moderate`` and
``test_a_track_editor_cannot_moderate`` are the tests that fail if anyone
"simplifies" the action back onto the comment prefix.

Editing is untouched and stays author-only: removing another person's comment
and rewriting it are different acts.

These drive the real path — a real ShareLink minted through the real settings
endpoint, and an anonymous POST through the real public handler with a request
that carries no identity at all. Constructing a ``Comment(author_id="public")``
by hand would pass just as well while proving nothing about whether the public
endpoint still produces that shape.
"""

from __future__ import annotations

import pytest

from app.api.errors import InsufficientPermissionsError
from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF, OWNS
from app.models.nodes import App, Comment, Entry, Track, User, Workspace

pytestmark = pytest.mark.smoke


class _AnonymousRequest:
    """A visitor's request: a JSON body and nothing else.

    No ``state.user``, no Authorization header — if the handler ever starts
    reading an identity, this raises rather than silently borrowing the
    test's own principal.
    """

    def __init__(self, body: dict) -> None:
        self._body = body

    async def json(self) -> dict:
        return self._body


async def _auth_user(email: str, name: str):
    """An auth principal plus its graph User node."""
    from jvspatial.api.auth.models import UserCreate

    from app.api.auth import _get_auth_service
    from app.models.nodes import User as UserNode
    from app.services.app_graph import catalog_user

    resp = await _get_auth_service().register_user(
        UserCreate(email=email, password="testpassword123")
    )
    node = await UserNode.create(user_id=resp.id, display_name=name)
    await catalog_user(node)
    return resp.id, node


async def _org(label: str):
    """Org workspace → app → track → entry, fully edge-wired (I-GRAPH-01)."""
    ws = await Workspace.create(
        kind="organization", name=f"Org {label}", name_fold=f"org {label}"
    )
    app = await App.create(
        name=f"App {label}", name_fold=f"app {label}", workspace_id=ws.id
    )
    track = await Track.create(
        title=f"Track {label}", title_fold=f"track {label}", workspace_id=ws.id
    )
    entry = await Entry.create(title=f"Entry {label}", track_id=track.id)
    await ws.connect(app, edge=CONTAINS)
    await app.connect(track, edge=CONTAINS)
    await track.connect(entry, edge=CONTAINS)
    return ws, app, track, entry


async def _owned_org(label: str):
    """The common fixture: an org whose owner administers the app."""
    ws, app, track, entry = await _org(label)
    owner_auth, owner = await _auth_user(f"mod-owner-{label}@example.com", "Owner")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="admin")
    await owner.connect(app, edge=OWNS)
    await owner.connect(track, edge=OWNS)
    return ws, app, track, entry, owner_auth, owner


async def _collaborator(ws: Workspace, track: Track, label: str, role: str):
    """A real workspace member holding ``role`` on the track.

    The workspace membership matters: without it the org gate refuses the
    caller before the comment tier is ever consulted, and a test asserting
    "denied" would pass for the wrong reason — proving nothing about the tier
    it claims to pin.
    """
    auth_id, node = await _auth_user(f"mod-{label}@example.com", label.title())
    await node.connect(ws, edge=IS_MEMBER_OF, role="member")
    await node.connect(track, edge=COLLABORATES_ON, role=role)
    return auth_id, node


async def _post_public_comment(track: Track, entry: Entry, owner_auth: str, text: str):
    """Enable public commenting, then post as a genuine anonymous visitor."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.tracks_public_share import (
        create_public_track_entry_comment,
        update_public_track_share_settings,
    )

    settings = await invoke_route_in_process(
        update_public_track_share_settings,
        principal_id=owner_auth,
        track_id=track.id,
        json_body={
            "enabled": True,
            "public_permissions": {"read_entries": True, "create_comments": True},
        },
    )
    token = settings["token"]
    assert token, "public link was not minted — the rest of this test is vacuous"

    result = await create_public_track_entry_comment(
        _AnonymousRequest({"text": text}),
        token=token,
        entry_id=entry.id,
    )
    comment = result["comment"]
    # The whole reason the bug existed. If this ever stops being "public",
    # these tests are no longer about the reported failure.
    assert comment["author_id"] == "public"
    return comment["id"]


async def _may(principal_id: str, entry: Entry, action: str) -> bool:
    """Evaluate as the endpoints do: entry-kind resource, track-scoped."""
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate as policy_evaluate

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=principal_id),
        action=action,
        resource=Resource(
            kind="entry", id=entry.id, scope=f"track:{entry.track_id or ''}"
        ),
    )
    return decision.allowed


async def _comment_ids_on(entry: Entry) -> list:
    comments = await entry.nodes(edge=["HAS_COMMENT"], node=["Comment"])
    return [c.id for c in comments]


@pytest.mark.asyncio
async def test_the_track_owner_can_delete_a_public_visitors_comment():
    """The reported gap, end to end."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.comments import delete_comment

    _ws, _app, track, entry, owner_auth, _owner = await _owned_org("ownerdel")
    comment_id = await _post_public_comment(
        track,
        entry,
        owner_auth,
        "visitor comment that the owner must be able to remove",
    )
    assert comment_id in await _comment_ids_on(entry)

    result = await invoke_route_in_process(
        delete_comment, principal_id=owner_auth, comment_id=comment_id
    )

    assert result["deleted_comment_id"] == comment_id
    assert await _comment_ids_on(entry) == []
    assert await Comment.get(comment_id) is None


@pytest.mark.asyncio
async def test_a_track_commenter_cannot_moderate():
    """The tier boundary: participating is not moderating.

    If ``comment.moderate`` were allowed to match ``_COMMENT_TIER_PREFIXES``,
    every commenter on the track could delete every other person's comments.
    """
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.comments import delete_comment

    ws, _app, track, entry, owner_auth, _owner = await _owned_org("commenterdel")
    comment_id = await _post_public_comment(track, entry, owner_auth, "visitor comment")

    heckler_auth, _heckler = await _collaborator(ws, track, "heckler", "commenter")
    # Attribution guard: this caller can post on the entry, so the refusal
    # below is the moderation tier talking and not the org gate or the
    # comment tier.
    assert await _may(heckler_auth, entry, "comment.create") is True

    with pytest.raises(InsufficientPermissionsError):
        await invoke_route_in_process(
            delete_comment, principal_id=heckler_auth, comment_id=comment_id
        )

    assert comment_id in await _comment_ids_on(entry)


@pytest.mark.asyncio
async def test_a_track_editor_cannot_moderate():
    """Editors mutate entries; removing another person's words is governance.

    Pinned separately from the commenter case because "editor+" is the obvious
    place for someone to relax this to, and that would hand comment removal to
    every content editor on the track.
    """
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.comments import delete_comment

    ws, _app, track, entry, owner_auth, _owner = await _owned_org("editordel")
    comment_id = await _post_public_comment(track, entry, owner_auth, "visitor comment")

    editor_auth, _editor = await _collaborator(ws, track, "editor", "editor")
    # Attribution guard: a real editor — they may mutate the entry itself, and
    # are still refused the comment. Without this the test would pass just as
    # well for a user with no access at all.
    assert await _may(editor_auth, entry, "entry.update") is True

    with pytest.raises(InsufficientPermissionsError):
        await invoke_route_in_process(
            delete_comment, principal_id=editor_auth, comment_id=comment_id
        )

    assert comment_id in await _comment_ids_on(entry)


@pytest.mark.asyncio
async def test_an_unrelated_user_cannot_delete_a_public_comment():
    """No grant at all — the moderation path must not become a hole."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.comments import delete_comment

    _ws, _app, track, entry, owner_auth, _owner = await _owned_org("strangerdel")
    comment_id = await _post_public_comment(track, entry, owner_auth, "visitor comment")

    stranger_auth, _stranger = await _auth_user("mod-stranger@example.com", "Stranger")

    with pytest.raises(InsufficientPermissionsError):
        await invoke_route_in_process(
            delete_comment, principal_id=stranger_auth, comment_id=comment_id
        )

    assert comment_id in await _comment_ids_on(entry)


@pytest.mark.asyncio
async def test_an_author_can_still_delete_their_own_comment():
    """The path that already worked, and must keep working without a grant.

    A plain commenter has no admin rank anywhere — so if the author branch ever
    regressed into the moderation branch, this is what would catch it.
    """
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.comments import create_comment, delete_comment

    ws, _app, track, entry, _owner_auth, _owner = await _owned_org("selfdel")
    author_auth, _author = await _collaborator(ws, track, "author", "commenter")

    created = await invoke_route_in_process(
        create_comment,
        principal_id=author_auth,
        entry_id=entry.id,
        text="my own comment",
    )
    comment_id = created["comment"]["id"]

    result = await invoke_route_in_process(
        delete_comment, principal_id=author_auth, comment_id=comment_id
    )

    assert result["deleted_comment_id"] == comment_id
    assert await Comment.get(comment_id) is None


@pytest.mark.asyncio
async def test_an_admin_still_cannot_edit_someone_elses_comment():
    """Moderation is removal, not rewriting. ``update_comment`` stays author-only."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.comments import update_comment

    _ws, _app, track, entry, owner_auth, _owner = await _owned_org("noedit")
    comment_id = await _post_public_comment(track, entry, owner_auth, "visitor comment")

    with pytest.raises(InsufficientPermissionsError):
        await invoke_route_in_process(
            update_comment,
            principal_id=owner_auth,
            comment_id=comment_id,
            text="words the visitor never wrote",
        )

    survivor = await Comment.get(comment_id)
    assert survivor is not None
    assert survivor.text == "visitor comment"


@pytest.mark.asyncio
async def test_a_moderated_delete_is_distinguishable_in_the_audit_trail(monkeypatch):
    """ "Admin removed a visitor's comment" and "author removed their own" are
    the same action verb. Without the marker the audit trail cannot tell them
    apart, which is exactly the pair a moderation feature needs to separate."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api import comments as comments_api

    captured: list = []

    async def _capture(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(comments_api, "emit_change_event", _capture)

    ws, _app, track, entry, owner_auth, _owner = await _owned_org("audit")
    comment_id = await _post_public_comment(track, entry, owner_auth, "visitor comment")
    await invoke_route_in_process(
        comments_api.delete_comment, principal_id=owner_auth, comment_id=comment_id
    )

    deletes = [c for c in captured if c.get("action") == "comment.delete"]
    assert len(deletes) == 1
    assert deletes[0]["before"]["_moderated"] is True

    # And the self-delete half, so the marker is a discriminator rather than a
    # constant that happens to read True.
    captured.clear()
    author_auth, _author = await _collaborator(ws, track, "selfaudit", "commenter")
    created = await invoke_route_in_process(
        comments_api.create_comment,
        principal_id=author_auth,
        entry_id=entry.id,
        text="my own comment",
    )
    await invoke_route_in_process(
        comments_api.delete_comment,
        principal_id=author_auth,
        comment_id=created["comment"]["id"],
    )

    deletes = [c for c in captured if c.get("action") == "comment.delete"]
    assert len(deletes) == 1
    assert deletes[0]["before"]["_moderated"] is False


@pytest.mark.asyncio
async def test_the_comments_read_advertises_moderation_rights():
    """The UI's delete affordance reads this flag rather than deciding for itself.

    Computed from the same action ``delete_comment`` enforces, so the control
    cannot drift out of step with the gate — a client deciding on its own would
    either hide a button that works or show one that 403s.
    """
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.comments import get_entry_comments

    ws, _app, track, entry, owner_auth, _owner = await _owned_org("flag")
    await _post_public_comment(track, entry, owner_auth, "visitor comment")
    commenter_auth, _c = await _collaborator(ws, track, "flagcommenter", "commenter")

    as_owner = await invoke_route_in_process(
        get_entry_comments, principal_id=owner_auth, entry_id=entry.id
    )
    as_commenter = await invoke_route_in_process(
        get_entry_comments, principal_id=commenter_auth, entry_id=entry.id
    )

    assert as_owner["can_moderate"] is True
    assert as_commenter["can_moderate"] is False
    # Both still see the comment itself — the flag is about the control, not
    # about visibility.
    assert as_owner["total"] == 1
    assert as_commenter["total"] == 1
