"""Schema-agnostic id → human-label resolution (chat/staging usability)."""

from __future__ import annotations

import pytest

from app.services import id_resolver


def test_find_ids_extracts_and_ignores_placeholders():
    text = (
        "In n.Workspace.5280a8e4b5ec43a2b76e38ff, create a track in {{app.id}} "
        "and link n.Entry.c602f48625754ad28f4079ec (dup n.Entry.c602f48625754ad28f4079ec)."
    )
    ids = id_resolver.find_ids(text)
    assert ids == [
        "n.Workspace.5280a8e4b5ec43a2b76e38ff",
        "n.Entry.c602f48625754ad28f4079ec",
    ]
    # {{app.id}} template token is NOT an id.
    assert "{{app.id}}" not in "".join(ids)


def test_find_ids_none_for_plain_text():
    assert id_resolver.find_ids("just a plain sentence, no ids") == []


def test_short_fallback():
    out = id_resolver._short("n.ContentProfile.fb8b80b1497b470aa2c5447b")
    assert out == "ContentProfile …447b"


def test_pick_label_priority():
    class _N:  # title beats name
        title = "My Track"
        name = "ignored"

    assert id_resolver._pick_label(_N(), "n.Track.x") == "My Track"

    class _A:  # name when no title
        name = "Acme Inc."

    assert id_resolver._pick_label(_A(), "n.Workspace.x") == "Acme Inc."


def test_pick_label_user_special_case():
    class User:  # class name drives the User branch
        display_name = "Eldon Marks"
        email = "e@x.com"

    assert id_resolver._pick_label(User(), "o.User.x") == "Eldon Marks"

    class AuthUser:
        display_name = ""
        email = "fallback@x.com"

    assert id_resolver._pick_label(AuthUser(), "o.User.x") == "fallback@x.com"


def test_pick_label_snippets_long_text():
    class Comment:
        text = "x" * 200

    out = id_resolver._pick_label(Comment(), "n.Comment.x")
    assert len(out) <= id_resolver._SNIPPET_MAX and out.endswith("…")


@pytest.mark.asyncio
async def test_humanize_ids_replaces_via_resolver(monkeypatch):
    async def fake_resolve(ids):
        return {
            "n.Workspace.5280a8e4b5ec43a2b76e38ff": "Acme Inc.",
            "n.Entry.c602f48625754ad28f4079ec": "Founder",
        }

    monkeypatch.setattr(id_resolver, "resolve_id_labels", fake_resolve)

    out = await id_resolver.humanize_ids(
        "You are in n.Workspace.5280a8e4b5ec43a2b76e38ff; "
        "employee n.Entry.c602f48625754ad28f4079ec is on leave."
    )
    assert out == "You are in Acme Inc.; employee Founder is on leave."


@pytest.mark.asyncio
async def test_humanize_ids_idempotent_and_safe(monkeypatch):
    async def fake_resolve(ids):
        return {i: "Name" for i in ids}

    monkeypatch.setattr(id_resolver, "resolve_id_labels", fake_resolve)
    once = await id_resolver.humanize_ids("see n.Entry.aaaaaaaaaaaaaaaaaaaaaaaa")
    assert once == "see Name"
    # Re-running over already-humanized text is a no-op (no id shapes left).
    assert await id_resolver.humanize_ids(once) == once


# ---------------------------------------------------------------------------
# Staging batch-placeholder prettify + streamed-delta humanizer
# ---------------------------------------------------------------------------


def test_prettify_batch_placeholders():
    from app.agentive.staging import _prettify_placeholders

    assert _prettify_placeholders("track in {{app.id}}") == "track in the new app"
    assert _prettify_placeholders("tag {{tag.id}}") == "tag the new tag"
    assert (
        _prettify_placeholders("ref {{step_2.id}} here") == "ref step 2's result here"
    )
    assert _prettify_placeholders("no placeholders") == "no placeholders"


@pytest.mark.asyncio
async def test_delta_humanizer_buffers_across_chunks(monkeypatch):
    """An id split across deltas is only released (and humanized) once whole."""
    from app.services import chat_streaming

    async def fake_humanize(text):
        return text.replace("n.Workspace.5280a8e4b5ec43a2b76e38ff", "Acme Inc.")

    monkeypatch.setattr(
        "app.services.id_resolver.humanize_ids", fake_humanize, raising=True
    )

    hz = chat_streaming._DeltaHumanizer()
    out_parts = []
    # The id streams as continuous tokens (no internal whitespace), split here
    # across deltas; only " now." introduces the whitespace that releases it.
    for frag in [
        "You ",
        "are ",
        "in ",
        "n.Wo",
        "rkspace.",
        "5280a8e4b5ec43a2b76e38ff",
        " now.",
    ]:
        r = await hz.feed(frag)
        if r:
            out_parts.append(r)
    out_parts.append(await hz.flush() or "")
    joined = "".join(p for p in out_parts if p)
    assert "n.Workspace." not in joined
    assert joined == "You are in Acme Inc. now."


@pytest.mark.asyncio
async def test_content_profile_label_via_owning_track(monkeypatch):
    """A draft profile names itself by the track it shapes (via its parent)."""

    class _CP:
        id = "n.ContentProfile.draft1"
        library_package = False
        draft_of_id = "n.ContentProfile.parent"
        name = "Default"

    class _Track:
        title = "Opportunities"

    async def fake_track_find(query):
        if (
            query.get("context.attached_content_profile_id")
            == "n.ContentProfile.parent"
        ):
            return [_Track()]
        return []

    async def fake_app_find(query):
        return []

    monkeypatch.setattr("app.models.nodes.Track.find", staticmethod(fake_track_find))
    monkeypatch.setattr("app.models.nodes.App.find", staticmethod(fake_app_find))

    assert (
        await id_resolver._content_profile_label(_CP()) == "the Opportunities profile"
    )


@pytest.mark.asyncio
async def test_content_profile_label_library_keeps_own_name():
    class _CP:
        id = "n.ContentProfile.lib"
        library_package = True
        name = "hr_app"

    assert await id_resolver._content_profile_label(_CP()) == "hr_app"


@pytest.mark.asyncio
async def test_resolve_user_ids_via_principal_resolver(monkeypatch):
    """o.User / n.User ids route through the canonical principal resolver."""

    class _User:
        display_name = "Eldon Marks"

    async def fake_batch_users(ids):
        return {i: _User() for i in ids}

    monkeypatch.setattr(
        "app.services.permissions.batch_resolve_users_by_principal_ids",
        fake_batch_users,
    )
    # No node ids → the node/object branches are skipped entirely.
    out = await id_resolver.resolve_id_labels(["o.User.986efd658db44746907d2a9f"])
    assert out == {"o.User.986efd658db44746907d2a9f": "Eldon Marks"}


@pytest.mark.asyncio
async def test_humanize_ids_preserves_markdown_link_targets(monkeypatch):
    track_id = "n.Track.aaaaaaaaaaaaaaaaaaaaaaaa"
    entry_id = "n.Entry.bbbbbbbbbbbbbbbbbbbbbbbb"

    async def fake_resolve(ids):
        return {i: "Human Label" for i in ids}

    monkeypatch.setattr(id_resolver, "resolve_id_labels", fake_resolve)

    text = (
        f"Open [Acme deal](/tracks/{track_id}?entry={entry_id}) "
        f"and also mention {track_id} in prose."
    )
    out = await id_resolver.humanize_ids(text)
    assert f"/tracks/{track_id}?entry={entry_id}" in out
    assert "[Acme deal]" in out
    assert "mention Human Label in prose." in out


@pytest.mark.asyncio
async def test_humanize_ids_preserves_bare_internal_routes(monkeypatch):
    track_id = "n.Track." + "c" * 24

    async def fake_resolve(ids):
        return {track_id: "Roadmap"}

    monkeypatch.setattr(id_resolver, "resolve_id_labels", fake_resolve)

    text = f"Go to /tracks/{track_id} or discuss {track_id}."
    out = await id_resolver.humanize_ids(text)
    assert f"/tracks/{track_id}" in out
    assert out.endswith("discuss Roadmap.")
