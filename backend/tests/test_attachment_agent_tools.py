"""Agent-facing attachment read tools are wired, dispatchable, and shaped right."""

from __future__ import annotations

import pytest
import yaml

from app.agentive.tooling import bindings


class _Allow:
    allowed = True


class _Deny:
    allowed = False


async def _seed_entry_with_attachment(text: str):
    """Create an Entry + Attachment + HAS_ATTACHMENT edge in the test graph."""
    from app.models.edges import CONTAINS, HAS_ATTACHMENT
    from app.models.nodes import Attachment, Entry, Track

    track = await Track.create(title="Files", visibility="private")
    entry = await Entry.create(title="Has files", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    att = await Attachment.create(
        filename="report.pdf",
        mime_type="application/pdf",
        size=2048,
        source_type="file",
        storage_key="k",
        page_count=2,
        metadata_status="complete",
        metadata={"common": {"title": "Report"}},
        extracted_text=text,
    )
    await entry.connect(att, edge=HAS_ATTACHMENT)
    return entry, att


def test_attachment_read_tools_are_bound():
    """Both read tools resolve to the agent service (not the old None stub)."""
    for name in ("integral_list_attachments", "integral_get_attachment_text"):
        b = bindings.TOOL_BINDINGS.get(name)
        assert b is not None, f"{name} missing from TOOL_BINDINGS"
        assert b.service_ref is not None, f"{name} still a None placeholder"
        assert b.service_param_map is not None
        # Resolves to a real callable in app.services.attachment_agent.
        assert callable(b.service_ref())


def test_attachment_read_tools_are_dispatchable_in_catalogue():
    import app.models.nodes  # noqa: F401 — register node classes
    from app.agentive.tooling.catalogue import build_tool_catalogue

    names = {t.get("name") for t in build_tool_catalogue()}
    assert "integral_list_attachments" in names
    assert "integral_get_attachment_text" in names


def test_attachment_read_tools_manifest_status_existing():
    """Manifest marks the read tools live; attach_file stays gap (read+deliver v1)."""
    with open("app/agentive/tool_manifest.yaml") as fh:
        manifest = yaml.safe_load(fh)
    tools = {t["name"]: t for t in manifest["domains"]["J_attachments"]["tools"]}
    assert tools["integral_list_attachments"]["status"] == "existing"
    assert tools["integral_get_attachment_text"]["status"] == "existing"
    assert tools["integral_attach_file"]["status"] == "existing"


@pytest.mark.asyncio
async def test_list_omits_extracted_text_body(monkeypatch):
    """The list summarises the body (has_text/text_length), never ships it."""
    from app.services import attachment_agent

    monkeypatch.setattr(
        attachment_agent, "policy_evaluate", lambda **kw: _async(_Allow())
    )
    entry, _ = await _seed_entry_with_attachment("x" * 500)
    out = await attachment_agent.list_attachments_for_entry(
        user_id="o.User.test", entry_id=entry.id
    )
    assert out["_kind"] == "attachment_list"
    assert out["total"] == 1
    item = out["attachments"][0]
    assert "extracted_text" not in item  # body stripped
    assert item["has_text"] is True
    assert item["text_length"] == 500
    assert item["download_url"]  # delivery URL present
    # Reply hint steers the model away from "above" (card renders below).
    hint = out["assistant_reply_hint"]
    assert "below" in hint and "above" in hint  # mentions both to forbid "above"


@pytest.mark.asyncio
async def test_empty_list_has_no_reply_hint(monkeypatch):
    """An entry with no files emits no card-phrasing hint."""
    from app.models.edges import CONTAINS
    from app.models.nodes import Entry, Track
    from app.services import attachment_agent

    monkeypatch.setattr(
        attachment_agent, "policy_evaluate", lambda **kw: _async(_Allow())
    )
    track = await Track.create(title="Empty", visibility="private")
    entry = await Entry.create(title="No files", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    out = await attachment_agent.list_attachments_for_entry(
        user_id="o.User.test", entry_id=entry.id
    )
    assert out["total"] == 0
    assert "assistant_reply_hint" not in out


@pytest.mark.asyncio
async def test_list_track_attachments_unions_all_entries(monkeypatch):
    """June 29 QA #6: the track-level lister returns files from EVERY entry in
    the track in one call, tagged with their parent entry — not just one page."""
    from app.models.edges import CONTAINS, HAS_ATTACHMENT
    from app.models.nodes import Attachment, Entry, Track
    from app.services import attachment_agent

    monkeypatch.setattr(
        attachment_agent, "policy_evaluate", lambda **kw: _async(_Allow())
    )

    track = await Track.create(title="Docs", visibility="private")
    # Three entries, two with files, one without — the union should be 3 files.
    for i in range(3):
        entry = await Entry.create(title=f"E{i}", track_id=track.id)
        await track.connect(entry, edge=CONTAINS)
        if i < 2:
            for j in range(2 if i == 0 else 1):
                att = await Attachment.create(
                    filename=f"f{i}-{j}.pdf",
                    mime_type="application/pdf",
                    size=10,
                    source_type="file",
                    storage_key="k",
                    metadata_status="complete",
                    metadata={},
                    extracted_text="body",
                )
                await entry.connect(att, edge=HAS_ATTACHMENT)

    out = await attachment_agent.list_attachments_for_track(
        user_id="o.User.test", track_id=track.id
    )
    assert out["_kind"] == "attachment_list"
    assert out["track_id"] == track.id
    assert out["total"] == 3, out  # 2 from E0 + 1 from E1 + 0 from E2
    # Every item carries its parent entry ref and omits the heavy body.
    for item in out["attachments"]:
        assert item.get("entry_id")
        assert "entry_title" in item
        assert "extracted_text" not in item


@pytest.mark.asyncio
async def test_list_track_attachments_denied_without_track_read(monkeypatch):
    """A caller without track.read is refused the whole-track listing."""
    from app.models.nodes import Track
    from app.services import attachment_agent

    monkeypatch.setattr(
        attachment_agent, "policy_evaluate", lambda **kw: _async(_Deny())
    )
    track = await Track.create(title="Private", visibility="private")
    with pytest.raises(PermissionError):
        await attachment_agent.list_attachments_for_track(
            user_id="o.User.stranger", track_id=track.id
        )


def test_list_track_attachments_tool_is_bound_and_manifested():
    """The track-level tool resolves to the service and is manifest-declared."""
    b = bindings.TOOL_BINDINGS.get("integral_list_track_attachments")
    assert b is not None and b.service_ref is not None
    assert callable(b.service_ref())
    with open("app/agentive/tool_manifest.yaml") as fh:
        manifest = yaml.safe_load(fh)
    tools = {t["name"]: t for t in manifest["domains"]["J_attachments"]["tools"]}
    assert tools["integral_list_track_attachments"]["status"] == "existing"
    assert tools["integral_list_track_attachments"]["policy_action"] == "track.read"


def test_list_workspace_attachments_tool_is_bound_and_manifested():
    """The workspace-level tool resolves to the service and is manifest-declared."""
    b = bindings.TOOL_BINDINGS.get("integral_list_workspace_attachments")
    assert b is not None and b.service_ref is not None
    assert callable(b.service_ref())
    with open("app/agentive/tool_manifest.yaml") as fh:
        manifest = yaml.safe_load(fh)
    tools = {t["name"]: t for t in manifest["domains"]["J_attachments"]["tools"]}
    assert tools["integral_list_workspace_attachments"]["status"] == "existing"
    assert (
        tools["integral_list_workspace_attachments"]["policy_action"]
        == "workspace.read"
    )


@pytest.mark.asyncio
async def test_list_workspace_attachments_unions_across_tracks(monkeypatch):
    """Workspace lister returns the union of attachments across accessible tracks."""
    from app.models.edges import CONTAINS, HAS_ATTACHMENT, IS_MEMBER_OF, OWNS
    from app.models.nodes import Attachment, Entry, Track, User, Workspace
    from app.services import attachment_agent

    monkeypatch.setattr(
        attachment_agent, "policy_evaluate", lambda **kw: _async(_Allow())
    )

    user = await User.create(user_id="ws_att_user", display_name="WS Att")
    ws = await Workspace.create(kind="personal", name="Files WS", name_fold="files ws")
    await user.connect(
        ws, edge=IS_MEMBER_OF, role="owner", joined_at="2026-01-01T00:00:00"
    )

    track_a = await Track.create(
        title="A", visibility="private", workspace_id=ws.id, owner_id=user.id
    )
    track_b = await Track.create(
        title="B", visibility="private", workspace_id=ws.id, owner_id=user.id
    )
    await user.connect(track_a, edge=OWNS)
    await user.connect(track_b, edge=OWNS)

    async def _seed(track, title, filename):
        entry = await Entry.create(title=title, track_id=track.id)
        await track.connect(entry, edge=CONTAINS)
        att = await Attachment.create(
            filename=filename,
            mime_type="text/plain",
            size=10,
            source_type="file",
            storage_key="k",
            metadata_status="complete",
            extracted_text="hi",
        )
        await entry.connect(att, edge=HAS_ATTACHMENT)
        return entry

    await _seed(track_a, "E1", "a.txt")
    await _seed(track_b, "E2", "b.txt")

    # Other-workspace track must not leak into the listing.
    other = await Track.create(
        title="Other", visibility="private", workspace_id="n.Workspace.other"
    )
    await user.connect(other, edge=OWNS)
    await _seed(other, "E3", "other.txt")

    out = await attachment_agent.list_attachments_for_workspace(
        user_id=user.id, workspace_id=ws.id
    )
    assert out["_kind"] == "attachment_list"
    assert out["workspace_id"] == ws.id
    assert out["total"] == 2, out
    names = {a["filename"] for a in out["attachments"]}
    assert names == {"a.txt", "b.txt"}
    for item in out["attachments"]:
        assert item.get("track_id") in {track_a.id, track_b.id}
        assert item.get("entry_id")
        assert "extracted_text" not in item


@pytest.mark.asyncio
async def test_get_text_caps_and_flags_untrusted(monkeypatch):
    """get_attachment_text truncates to max_chars and marks content untrusted."""
    from app.services import attachment_agent

    monkeypatch.setattr(
        attachment_agent, "policy_evaluate", lambda **kw: _async(_Allow())
    )
    _, att = await _seed_entry_with_attachment("y" * 50)
    out = await attachment_agent.get_attachment_text(
        user_id="o.User.test", attachment_id=att.id, max_chars=10
    )
    assert out["truncated"] is True
    assert out["char_count"] == 10
    assert len(out["text"]) == 10
    assert out["content_untrusted"] is True


@pytest.mark.asyncio
async def test_list_denied_without_entry_read(monkeypatch):
    """A caller without entry.read is refused."""
    from app.services import attachment_agent

    monkeypatch.setattr(
        attachment_agent, "policy_evaluate", lambda **kw: _async(_Deny())
    )
    entry, _ = await _seed_entry_with_attachment("z")
    with pytest.raises(PermissionError):
        await attachment_agent.list_attachments_for_entry(
            user_id="o.User.stranger", entry_id=entry.id
        )


def _async(value):
    async def _coro():
        return value

    return _coro()


async def _add_attachment(entry, **overrides):
    """Connect an extra Attachment to ``entry`` for multi-file / blocked tests."""
    from app.models.edges import HAS_ATTACHMENT
    from app.models.nodes import Attachment

    kwargs = {
        "filename": "virus.pdf",
        "mime_type": "application/pdf",
        "size": 10,
        "source_type": "file",
        "storage_key": "b",
        "metadata_status": "complete",
        "extracted_text": "bad",
    }
    kwargs.update(overrides)
    att = await Attachment.create(**kwargs)
    await entry.connect(att, edge=HAS_ATTACHMENT)
    return att


@pytest.mark.asyncio
async def test_list_excludes_blocked_attachment(monkeypatch):
    """Blocked (failed-scan) attachments never appear in the agent list."""
    from app.services import attachment_agent

    monkeypatch.setattr(
        attachment_agent, "policy_evaluate", lambda **kw: _async(_Allow())
    )
    entry, _ = await _seed_entry_with_attachment("clean body")
    await _add_attachment(entry, scan_status="blocked")
    out = await attachment_agent.list_attachments_for_entry(
        user_id="o.User.test", entry_id=entry.id
    )
    names = [a["filename"] for a in out["attachments"]]
    assert out["total"] == 1
    assert "report.pdf" in names
    assert "virus.pdf" not in names


@pytest.mark.asyncio
async def test_get_text_blocked_is_not_found(monkeypatch):
    """A blocked attachment is invisible to get_attachment_text."""
    from app.services import attachment_agent

    monkeypatch.setattr(
        attachment_agent, "policy_evaluate", lambda **kw: _async(_Allow())
    )
    entry, _ = await _seed_entry_with_attachment("clean body")
    blocked = await _add_attachment(entry, scan_status="blocked")
    with pytest.raises(ValueError):
        await attachment_agent.get_attachment_text(
            user_id="o.User.test", attachment_id=blocked.id
        )


@pytest.mark.asyncio
async def test_get_text_passes_metadata_status(monkeypatch):
    """get_attachment_text surfaces metadata_status (skill gates honesty on it)."""
    from app.services import attachment_agent

    monkeypatch.setattr(
        attachment_agent, "policy_evaluate", lambda **kw: _async(_Allow())
    )
    entry, _ = await _seed_entry_with_attachment("clean body")
    pending = await _add_attachment(
        entry, filename="wip.pdf", metadata_status="pending", extracted_text=""
    )
    out = await attachment_agent.get_attachment_text(
        user_id="o.User.test", attachment_id=pending.id
    )
    assert out["metadata_status"] == "pending"
    assert out["char_count"] == 0
