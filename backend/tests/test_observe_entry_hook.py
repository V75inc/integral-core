"""The entry-save door of the Personal Context App — ADR-008.

A person saves an entry in any app, in any workspace; an observation lands
in the `stream` track of their Personal Context App, which lives in their
OWN personal workspace. Two capabilities the facade did not have, and one
of them — a create of any kind — did not exist for bundle tools at all.

The refusals matter more than the write, so most of this file is about them:

- an observation IS an entry, so observing this App's own tracks loops
  forever;
- `agent-scratch` is the resident's working memory about the WORK, not a
  record about the person;
- `attention_enabled: false` stops it at the source;
- an excluded workspace is invisible;
- and the new facade write is bounded to the calling bundle's own App, in
  the caller's own personal workspace, matched by manifest key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Module scope: these load .env as an import side effect, which trips
# conftest's env-leak guard at teardown if it happens inside a test body.
import app.api.entries  # noqa: F401
from app.models.edges import CONTAINS
from app.models.nodes import Entry, Workspace
from app.services.hooks.registry import ToolContext
from app.services.personal_context import (
    PERSONAL_CONTEXT_SLUG,
    provision_personal_context_app,
)

pytestmark = pytest.mark.library

_TOOL = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "profiles"
    / "personal_context"
    / "tools"
    / "observe_entry.py"
)


def _run():
    from app.profiles.personal_context.tools.observe_entry import run

    return run


async def _setup(test_user):
    app_node = await provision_personal_context_app(user_id=test_user.id)
    assert app_node is not None
    tracks = {
        str(getattr(t, "template_id", "") or ""): t
        for t in await app_node.nodes(edge=[CONTAINS], node=["Track"], direction="out")
    }
    return app_node, tracks


def _ctx(user_id: str, workspace_id: str, entry_id: str = "") -> ToolContext:
    """A context shaped like the one entry_save_runtime builds."""
    ctx = ToolContext(
        user_id=user_id,
        workspace_id=workspace_id,
        scope=f"entry:{entry_id}",
    )
    # Stamped by run_tool in production, from the REGISTERED spec.
    ctx.bundle_slug = PERSONAL_CONTEXT_SLUG
    return ctx


async def _stream_rows(tracks) -> list:
    return list((await Entry.find({"context.track_id": tracks["stream"].id})) or [])


async def _make_entry(user, workspace_id: str, title: str):
    """A plain track + entry outside the Personal Context App."""
    from datetime import datetime, timezone

    from app.models.edges import OWNS
    from app.models.nodes import Track
    from app.services.entry_writer import create_entry_internal

    now = datetime.now(timezone.utc).isoformat()
    track = await Track.create(
        title=title,
        title_fold=title.casefold(),
        owner_id=user.id,
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )
    await user.connect(track, edge=OWNS, role="owner", granted_at=now)
    created = await create_entry_internal(
        actor_kind="human",
        actor_id=user.id,
        payload={"track_id": track.id, "title": f"{title} record"},
    )
    return track, created["id"]


# ---------------------------------------------------------------------------
# The write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_entry_saved_elsewhere_lands_in_the_personal_stream(test_user):
    app_node, tracks = await _setup(test_user)
    before = len(await _stream_rows(tracks))

    # An entry in a DIFFERENT workspace from the one the App lives in.
    now_ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Client Org",
        name_fold="client org",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    from app.models.edges import IS_MEMBER_OF

    await test_user.connect(
        now_ws, edge=IS_MEMBER_OF, role="owner", joined_at="2026-01-01T00:00:00Z"
    )
    _, entry_id = await _make_entry(test_user, now_ws.id, "Client Work")

    result = await _run()(
        {"entry_id": entry_id, "hook_point": "entry.create"},
        _ctx(test_user.id, now_ws.id, entry_id),
    )

    assert result["observed"] is True, result
    rows = await _stream_rows(tracks)
    assert len(rows) == before + 1

    row = rows[-1]
    fields = dict(getattr(row, "custom_fields", None) or {})
    assert fields.get("surface") == "entry"
    assert fields.get("handled") == "pending"
    assert fields.get("entry_ref") == entry_id
    # Written into the person's own workspace, while recording where the
    # entry actually lives.
    assert fields.get("workspace_ref") == now_ws.id
    assert row.track_id == tracks["stream"].id
    assert getattr(await Workspace.get(app_node.workspace_id), "kind", "") == "personal"


@pytest.mark.asyncio
async def test_an_edit_reads_differently_from_a_create(test_user):
    _, tracks = await _setup(test_user)
    personal_ws = (await Workspace.get((await _setup(test_user))[0].workspace_id)).id
    _, entry_id = await _make_entry(test_user, personal_ws, "Notes")

    await _run()(
        {"entry_id": entry_id, "hook_point": "entry.update"},
        _ctx(test_user.id, personal_ws, entry_id),
    )
    titles = [r.title for r in await _stream_rows(tracks)]
    assert any(t.startswith("Edited") for t in titles), titles


# ---------------------------------------------------------------------------
# The refusals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_its_own_entries_are_never_observed(test_user):
    # An observation IS an entry. Without this the App observes its own
    # observations forever.
    app_node, tracks = await _setup(test_user)
    from app.services.entry_writer import create_entry_internal

    created = await create_entry_internal(
        actor_kind="human",
        actor_id=test_user.id,
        payload={"track_id": tracks["stream"].id, "title": "An observation"},
    )
    before = len(await _stream_rows(tracks))

    result = await _run()(
        {"entry_id": created["id"], "hook_point": "entry.create"},
        _ctx(test_user.id, app_node.workspace_id, created["id"]),
    )

    assert result["observed"] is False
    assert result["reason"] == "own_app"
    assert len(await _stream_rows(tracks)) == before


@pytest.mark.asyncio
async def test_agent_scratch_is_never_observed(test_user):
    # Scratch is the resident's working memory about the WORK. Folding it
    # into the record about the PERSON makes both less useful.
    from app.services.agent_scratch import provision_scratch_track
    from app.services.entry_writer import create_entry_internal

    app_node, tracks = await _setup(test_user)
    scratch = await provision_scratch_track(user_id=test_user.id)
    created = await create_entry_internal(
        actor_kind="human",
        actor_id=test_user.id,
        payload={"track_id": scratch.id, "title": "A working note"},
    )
    before = len(await _stream_rows(tracks))

    result = await _run()(
        {"entry_id": created["id"], "hook_point": "entry.create"},
        _ctx(test_user.id, app_node.workspace_id, created["id"]),
    )

    assert result["observed"] is False
    assert result["reason"] == "scratch"
    assert len(await _stream_rows(tracks)) == before


@pytest.mark.asyncio
async def test_attention_can_be_switched_off(test_user):
    app_node, tracks = await _setup(test_user)
    _, entry_id = await _make_entry(test_user, app_node.workspace_id, "Quiet")
    before = len(await _stream_rows(tracks))

    settings = dict(getattr(app_node, "settings", None) or {})
    settings["attention_enabled"] = False
    app_node.settings = settings
    await app_node.save()

    result = await _run()(
        {"entry_id": entry_id, "hook_point": "entry.create"},
        _ctx(test_user.id, app_node.workspace_id, entry_id),
    )

    assert result["observed"] is False
    assert result["reason"] == "attention_disabled"
    assert len(await _stream_rows(tracks)) == before


@pytest.mark.asyncio
async def test_an_excluded_workspace_is_invisible(test_user):
    app_node, tracks = await _setup(test_user)
    _, entry_id = await _make_entry(test_user, app_node.workspace_id, "Excluded")
    before = len(await _stream_rows(tracks))

    settings = dict(getattr(app_node, "settings", None) or {})
    settings["excluded_arenas"] = [app_node.workspace_id]
    app_node.settings = settings
    await app_node.save()

    result = await _run()(
        {"entry_id": entry_id, "hook_point": "entry.create"},
        _ctx(test_user.id, app_node.workspace_id, entry_id),
    )

    assert result["observed"] is False
    assert result["reason"] == "excluded"
    assert len(await _stream_rows(tracks)) == before


@pytest.mark.asyncio
async def test_a_failure_never_fails_the_save(test_user):
    # An entry save must not fail because attention failed. The tool returns
    # a result dict on every path rather than propagating.
    app_node, _ = await _setup(test_user)
    result = await _run()({}, _ctx(test_user.id, app_node.workspace_id))
    assert result["observed"] is False
    assert result["reason"] == "no_entry"

    result = await _run()(
        {"entry_id": "n.Entry.nope"}, _ctx(test_user.id, app_node.workspace_id)
    )
    assert result["observed"] is False


# ---------------------------------------------------------------------------
# The facade primitive's bounds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_write_is_bounded_to_the_calling_bundle(test_user):
    app_node, _ = await _setup(test_user)

    # A context claiming to be somebody else's bundle resolves nothing.
    ctx = _ctx(test_user.id, app_node.workspace_id)
    ctx.bundle_slug = "some-other-bundle"
    assert await ctx.own_bundle_view() is None
    assert (
        await ctx.create_entry_in_own_bundle_track("stream", title="Should not land")
        is None
    )

    # And a track key this bundle does not declare resolves nothing either.
    ctx = _ctx(test_user.id, app_node.workspace_id)
    assert (
        await ctx.create_entry_in_own_bundle_track("not_a_track", title="Nope") is None
    )


@pytest.mark.asyncio
async def test_a_tool_cannot_claim_another_bundle(test_user):
    # The slug is stamped by the DISPATCHER from the registered spec, never
    # supplied by the tool. This pins that the stamp is where it looks.
    src = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "hooks"
        / "tool_dispatch.py"
    ).read_text(encoding="utf-8")
    assert 'ctx.bundle_slug = str(spec.get("_bundle_slug") or "")' in src
    stamp = src.index("ctx.bundle_slug =")
    call = src.index("await fn(payload, ctx)")
    assert stamp < call, "the slug is stamped after the handler runs"


@pytest.mark.asyncio
async def test_another_users_workspace_is_not_reachable(test_user, test_user2):
    # own_bundle_view resolves the ACTING principal's own personal workspace,
    # never the one the hook fired in. Acting as user2 must not reach user1's.
    app_one, _ = await _setup(test_user)
    app_two = await provision_personal_context_app(user_id=test_user2.id)
    assert app_two is not None

    ctx = _ctx(str(test_user2.id), app_one.workspace_id)
    view = await ctx.own_bundle_view()
    assert view is not None
    assert view["workspace_id"] == app_two.workspace_id
    assert view["workspace_id"] != app_one.workspace_id


def test_the_tool_still_reaches_substrate_only_through_the_facade():
    import re

    src = _TOOL.read_text(encoding="utf-8")

    # The facade rule is about what the tool may REACH, not one import line.
    # Bundle tools take the context as a parameter and annotate it ``Any``
    # (all 12 shipped tools do; importing ToolContext is permitted by
    # .ci/bundle_facade_check.sh but is not the convention). So assert the
    # invariant directly: no substrate import of any kind.
    substrate_imports = [
        line
        for line in src.splitlines()
        if re.match(r"\s*(from|import)\s+app\.", line)
        and "from app.services.hooks.registry import ToolContext" not in line
    ]
    assert not substrate_imports, f"the tool bypasses the facade: {substrate_imports}"

    for forbidden in ("from app.models", "from app.services.entry", "Entry.find("):
        assert forbidden not in src, f"the tool bypasses the facade: {forbidden}"

    # ...and it still receives the context it is supposed to work through.
    assert "async def run(payload" in src and "ctx" in src


@pytest.mark.asyncio
async def test_the_row_says_the_agent_wrote_it(test_user):
    """Actor and source are different questions, and the row answers both.

    The audit actor is the human — their save caused it, it ran under their
    principal, their role on the track gated it. But the CONTENT came from
    the App, and provenance says so, or a compiled page could later cite an
    observation as something the person typed.
    """
    app_node, tracks = await _setup(test_user)
    _, entry_id = await _make_entry(test_user, app_node.workspace_id, "Provenance")

    result = await _run()(
        {"entry_id": entry_id, "hook_point": "entry.create"},
        _ctx(test_user.id, app_node.workspace_id, entry_id),
    )
    assert result["observed"] is True

    row = (await _stream_rows(tracks))[-1]
    prov = getattr(row, "provenance", None)
    source = getattr(prov, "source", None) or (
        prov.get("source") if isinstance(prov, dict) else None
    )
    assert source == "agent", f"the observation claims a human wrote it: {prov!r}"
    source_id = getattr(prov, "source_id", None) or (
        prov.get("source_id") if isinstance(prov, dict) else None
    )
    assert PERSONAL_CONTEXT_SLUG in str(source_id)


@pytest.mark.asyncio
async def test_a_context_with_no_bundle_resolves_nothing(test_user):
    """The slug is not optional.

    ``ToolContext`` is constructed at four sites and only the tool dispatcher
    stamps a bundle. A context built anywhere else — or a future call site
    that forgets — must resolve nothing rather than fall back to some
    default, which would let any caller write into a bundle's private
    tracks.
    """
    app_node, _ = await _setup(test_user)
    ctx = ToolContext(
        user_id=str(test_user.id),
        workspace_id=app_node.workspace_id,
        scope="entry:none",
    )
    assert ctx.bundle_slug == ""
    assert await ctx.own_bundle_view() is None
    assert await ctx.create_entry_in_own_bundle_track("stream", title="Nope") is None


@pytest.mark.asyncio
async def test_the_write_is_refused_without_rights_on_the_track(test_user):
    """Defence in depth, and deliberately kept.

    Today the target track is always in the caller's own personal workspace,
    which they own — so this gate cannot fire through the normal path. It
    stays because it is the check that keeps the primitive safe if
    ``own_bundle_view`` is ever widened past ``kind == "personal"``, and a
    permission check that only holds by luck of the surrounding code is not
    one worth relying on.
    """
    from unittest.mock import AsyncMock, patch

    app_node, tracks = await _setup(test_user)
    before = len(await _stream_rows(tracks))
    ctx = _ctx(test_user.id, app_node.workspace_id)

    # AsyncMock, not MagicMock: resolve_role is awaited, and a plain
    # return_value hands back a non-awaitable that raises inside the
    # method's own except block — the write would then be refused by the
    # error path rather than by the gate, and this test would pass while
    # proving nothing. (It did, until the mutation check caught it.)
    for denied_role in (None, "viewer", "commenter"):
        with patch(
            "app.services.permissions.resolve_role",
            new=AsyncMock(return_value=denied_role),
        ):
            assert (
                await ctx.create_entry_in_own_bundle_track(
                    "stream", title=f"Refused for {denied_role}"
                )
                is None
            ), f"role {denied_role!r} was allowed to write"
        assert len(await _stream_rows(tracks)) == before

    # ...and the gate is what refused, not something incidental: an allowed
    # role writes.
    with patch(
        "app.services.permissions.resolve_role", new=AsyncMock(return_value="editor")
    ):
        assert (
            await ctx.create_entry_in_own_bundle_track("stream", title="Allowed")
            is not None
        )
    assert len(await _stream_rows(tracks)) == before + 1


@pytest.mark.asyncio
async def test_the_observation_carries_an_entry_type(test_user):
    """A typeless entry exists in the database and nowhere a person looks.

    Found in the browser, not here: the DB had four observations and the
    Stream view showed one. ``create_entry_internal`` defaults ``type_id``
    to empty and does not resolve one — only the HTTP handler does — so
    every row this facade wrote was invisible to any view filtering on
    ``entry_type_keys``, which is every view this bundle declares.

    It is worse than invisibility. A relation field validates its target's
    entry type, so a typeless observation can never be cited as a
    ``sources`` value — which would have silently broken the explainability
    the whole App rests on.
    """
    app_node, tracks = await _setup(test_user)
    _, entry_id = await _make_entry(test_user, app_node.workspace_id, "Typed")

    result = await _run()(
        {"entry_id": entry_id, "hook_point": "entry.create"},
        _ctx(test_user.id, app_node.workspace_id, entry_id),
    )
    assert result["observed"] is True

    row = (await _stream_rows(tracks))[-1]
    type_id = str(getattr(row, "type_id", "") or "")
    assert type_id, "the observation has no entry type — no view will show it"

    from app.models.nodes import EntryType

    et = await EntryType.get(type_id)
    assert et is not None
    assert str(getattr(et, "name", "")).lower() == "observation"


@pytest.mark.asyncio
async def test_a_typed_observation_can_be_cited_as_a_source(test_user):
    """The chain the App exists to keep: fact -> sources -> observation.

    Relation validation resolves the target's EntryType and checks it
    against `target_entry_types`. A typeless target fails that check, so
    this is the test that would have caught the missing type even if no
    view ever filtered.
    """
    from app.services.entry_writer import create_entry_internal

    app_node, tracks = await _setup(test_user)
    _, entry_id = await _make_entry(test_user, app_node.workspace_id, "Citable")
    await _run()(
        {"entry_id": entry_id, "hook_point": "entry.create"},
        _ctx(test_user.id, app_node.workspace_id, entry_id),
    )
    observation = (await _stream_rows(tracks))[-1]

    from app.services.content_profile_entry_fields import _validate_relation_values

    identity = tracks["identity"]
    # The `sources` relation as the manifest declares it.
    relation = {
        "target": "entry",
        "target_track_types": ["stream"],
        "target_entry_types": ["observation"],
        "allow_cross_track": True,
        "many": True,
    }
    validated = await _validate_relation_values(
        relation=relation,
        value=[observation.id],
        source_track=identity,
        field_key="sources",
    )
    assert validated == [observation.id]

    # And the belief itself writes.
    created = await create_entry_internal(
        actor_kind="human",
        actor_id=str(test_user.id),
        payload={"track_id": identity.id, "title": "A belief"},
    )
    assert isinstance(created, dict) and created.get("id")
