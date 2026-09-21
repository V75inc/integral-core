"""B-AGENT-03 — every write-shaped staging kind is gated by the scope table.

``_validate_kind_scope`` used to carry four hand-maintained sets and missed
~13 kinds. A card staged in workspace A that carried an id the caller can also
reach in workspace B landed the write in B (the route handlers' ``resolve_role``
still applied, so a scope leak rather than a privilege escalation — but the
active workspace is meant to be a hard boundary).

These tests pin one representative kind per group plus a coverage assertion, so
a newly registered executor cannot silently skip the gate.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.agentive import staging_executors as se
from app.services.agent_scope import current_scope_workspace_id

WS = "n.Workspace.beta"
IN_SCOPE_TRACK = "n.Track.in-scope"
FOREIGN_TRACK = "n.Track.foreign"


class _Track:
    def __init__(self, id_: str) -> None:
        self.id = id_
        self.workspace_id = WS


class _Entry:
    def __init__(self, id_: str, track_id: str) -> None:
        self.id = id_
        self.track_id = track_id


@pytest.fixture
def scoped(monkeypatch):
    """Bind an active workspace whose only accessible track is IN_SCOPE_TRACK."""

    async def fake_tracks(user_id: str):
        return [_Track(IN_SCOPE_TRACK)]

    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks", fake_tracks
    )
    tok = current_scope_workspace_id.set(WS)
    yield
    current_scope_workspace_id.reset(tok)


def _patch_entries(monkeypatch, mapping: Dict[str, str]) -> None:
    """Map entry id -> track id for ``check_entry_in_active_scope``."""
    from app.models.nodes import Entry

    async def fake_get(entry_id: str):
        track_id = mapping.get(entry_id)
        return _Entry(entry_id, track_id) if track_id else None

    monkeypatch.setattr(Entry, "get", fake_get)


def _violation(err: Any) -> bool:
    return isinstance(err, dict) and err.get("error_code") == "scope_violation"


# ---------------------------------------------------------------------------
# entry-scoped kinds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_delete_entries_refuses_foreign_entry(scoped, monkeypatch):
    """Every id in ``entry_ids`` is gated — the bulk loop never re-enters dispatch."""
    _patch_entries(monkeypatch, {"e1": IN_SCOPE_TRACK, "e2": FOREIGN_TRACK})
    deleted: List[str] = []

    async def _fake_delete(user_id: str, payload: Dict[str, Any]):
        deleted.append(payload["entry_id"])
        return {"ok": True}

    monkeypatch.setattr(se, "_x_delete_entry", _fake_delete)
    res = await se.dispatch(
        user_id="u1", kind="bulk_delete_entries", payload={"entry_ids": ["e1", "e2"]}
    )
    assert _violation(res)
    assert deleted == [], "the gate must run BEFORE any entry is deleted"


@pytest.mark.asyncio
async def test_remove_entry_tag_refuses_foreign_entry(scoped, monkeypatch):
    """An untag card carrying a foreign entry id is refused at the gate."""
    _patch_entries(monkeypatch, {"e9": FOREIGN_TRACK})
    err = await se._validate_kind_scope(
        "remove_entry_tag", {"entry_id": "e9", "tag_id": "t1"}, user_id="u1"
    )
    assert _violation(err)


@pytest.mark.asyncio
async def test_link_entries_gates_both_ends(scoped, monkeypatch):
    """A foreign TARGET is refused even when the source is in scope."""
    _patch_entries(monkeypatch, {"src": IN_SCOPE_TRACK, "dst": FOREIGN_TRACK})
    err = await se._validate_kind_scope(
        "link_entries",
        {"source_entry_id": "src", "field_key": "rel", "target_id": "dst"},
        user_id="u1",
    )
    assert _violation(err)

    ok = await se._validate_kind_scope(
        "link_entries",
        {"source_entry_id": "src", "field_key": "rel", "target_id": "src"},
        user_id="u1",
    )
    assert ok is None


@pytest.mark.asyncio
async def test_delete_comment_resolves_comment_to_its_entry(scoped, monkeypatch):
    """A comment id names no scope of its own — it is resolved to its entry."""
    from app.models.nodes import Comment

    class _Comment:
        id = "c1"

        async def nodes(self, **_kwargs: Any) -> List[Any]:
            return [_Entry("e-foreign", FOREIGN_TRACK)]

    async def fake_get(comment_id: str):
        return _Comment() if comment_id == "c1" else None

    monkeypatch.setattr(Comment, "get", fake_get)
    _patch_entries(monkeypatch, {"e-foreign": FOREIGN_TRACK})

    err = await se._validate_kind_scope(
        "delete_comment", {"comment_id": "c1"}, user_id="u1"
    )
    assert _violation(err)
    # An unknown comment is left to the handler's own 404, not refused here.
    assert (
        await se._validate_kind_scope(
            "delete_comment", {"comment_id": "nope"}, user_id="u1"
        )
        is None
    )


# ---------------------------------------------------------------------------
# track-scoped kinds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_tag_refuses_foreign_track(scoped):
    """``create_tag`` names its track directly — gate it like the other track kinds."""
    err = await se._validate_kind_scope(
        "create_tag", {"name": "qa", "track_id": FOREIGN_TRACK}, user_id="u1"
    )
    assert _violation(err)
    ok = await se._validate_kind_scope(
        "create_tag", {"name": "qa", "track_id": IN_SCOPE_TRACK}, user_id="u1"
    )
    assert ok is None


@pytest.mark.asyncio
async def test_delete_view_resolves_view_to_its_track(scoped, monkeypatch):
    """A view id names no scope of its own — it is resolved to its track."""
    from app.models.nodes import View

    class _View:
        def __init__(self, track_id: str) -> None:
            self.id = "v1"
            self.track_id = track_id

    async def fake_get(view_id: str):
        return _View(FOREIGN_TRACK) if view_id == "v1" else None

    monkeypatch.setattr(View, "get", fake_get)
    err = await se._validate_kind_scope("delete_view", {"view_id": "v1"}, user_id="u1")
    assert _violation(err)


# ---------------------------------------------------------------------------
# resource-scoped kinds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_app_refuses_foreign_app(scoped, monkeypatch):
    """An app id reachable in another workspace must not be written here."""
    from app.models.nodes import App

    class _App:
        def __init__(self, workspace_id: str) -> None:
            self.id = "a1"
            self.workspace_id = workspace_id

    async def fake_get(app_id: str):
        return _App("n.Workspace.other" if app_id == "a-foreign" else WS)

    monkeypatch.setattr(App, "get", fake_get)

    err = await se._validate_kind_scope(
        "update_app", {"app_id": "a-foreign", "name": "X"}, user_id="u1"
    )
    assert _violation(err)
    ok = await se._validate_kind_scope(
        "update_app", {"app_id": "a-mine", "name": "X"}, user_id="u1"
    )
    assert ok is None


@pytest.mark.asyncio
async def test_create_track_refuses_foreign_app(scoped, monkeypatch):
    """A card staged in WS must not land a track under an app in another WS.

    ``track_service`` derives the workspace FROM the app and checks only
    ``app.update`` + ``can_create_track_under_workspace`` — both pass for any
    workspace the caller legitimately belongs to, so the staging gate is the
    only thing keeping the active workspace a hard boundary here.
    """
    from app.models.nodes import App

    class _App:
        def __init__(self, workspace_id: str) -> None:
            self.id = "a1"
            self.workspace_id = workspace_id

    async def fake_get(app_id: str):
        return _App("n.Workspace.other" if app_id == "a-foreign" else WS)

    monkeypatch.setattr(App, "get", fake_get)

    err = await se._validate_kind_scope(
        "create_track", {"title": "T", "app_id": "a-foreign"}, user_id="u1"
    )
    assert _violation(err)
    ok = await se._validate_kind_scope(
        "create_track", {"title": "T", "app_id": "a-mine"}, user_id="u1"
    )
    assert ok is None


@pytest.mark.asyncio
async def test_create_track_standalone_is_not_refused(scoped):
    """No ``app_id`` → nothing to gate; the executor binds the active scope.

    Guards the ``optional`` flag: the resource checker answers
    ``resource_id is required`` for an empty id, which would break every
    standalone track creation.
    """
    ok = await se._validate_kind_scope(
        "create_track", {"title": "Standalone"}, user_id="u1"
    )
    assert ok is None


@pytest.mark.asyncio
async def test_revoke_share_link_resolves_link_to_its_resource(scoped, monkeypatch):
    """The link carries its own resource_type/resource_id — read them off it."""
    from app.models.nodes import ShareLink

    class _Link:
        resource_type = "track"
        resource_id = FOREIGN_TRACK

    async def fake_get(link_id: str):
        return _Link() if link_id == "sl1" else None

    monkeypatch.setattr(ShareLink, "get", fake_get)

    err = await se._validate_kind_scope(
        "revoke_share_link", {"share_link_id": "sl1"}, user_id="u1"
    )
    assert _violation(err)
    assert (
        await se._validate_kind_scope(
            "revoke_share_link", {"share_link_id": "gone"}, user_id="u1"
        )
        is None
    )


@pytest.mark.asyncio
async def test_invite_uses_target_type_and_id(scoped):
    """Invites name their target under ``target_type`` / ``target_id``."""
    err = await se._validate_kind_scope(
        "invite",
        {"target_type": "track", "target_id": FOREIGN_TRACK, "email": "a@b.c"},
        user_id="u1",
    )
    assert _violation(err)


@pytest.mark.asyncio
async def test_no_active_scope_is_a_no_op():
    """Legacy union scope (no bound workspace) gates nothing."""
    assert (
        await se._validate_kind_scope(
            "update_app", {"app_id": "anything"}, user_id="u1"
        )
        is None
    )


# ---------------------------------------------------------------------------
# Coverage — a new executor must make an explicit scope decision
# ---------------------------------------------------------------------------

# Kinds deliberately NOT in the scope table: they carry no workspace-bound
# target id in their payload (their own executor / handler is the gate), or the
# workspace is supplied by the active scope rather than read off the payload.
_INTENTIONALLY_UNGATED = {
    "batch",  # gated per sub-op, when each is dispatched
    "create_app",  # workspace comes FROM the active scope
    # Same reason: dispatch binds workspace_id from the ACTIVE scope when it
    # mints the card (never from model args), and the executor re-enters
    # mcp_proxy, whose connector-subject policy and workspace-authority checks
    # both run again at bless time.
    "mcp_tool_call",
    "apply_library_operational_model",
    "author_operational_model",
    "draft_new_profile",
    "propose_profile_revision",
    "apply_to_draft",
    "publish_profile_draft",
    "discard_profile_draft",
    "modify_operational_model.add_entry_type",
    "modify_operational_model.remove_entry_type",
    "modify_operational_model.add_view",
    "modify_operational_model.remove_view",
    "modify_operational_model.add_tag",
    "modify_operational_model.remove_tag",
    "author_skill",
    "update_skill",
    "delete_skill",
    "resolve_conflict",
    "trigger_sync",
    "routine_task_create",
    "routine_task_update",
    "routine_task_cancel",
    "routine_task_purge",
    # Workspace-bound bundle tool invoke: workspace from active scope at stage
    # time; executor re-checks trust_tier + ToolContext at bless.
    "call_workspace_tool",
    # Thread ownership is the authority boundary; the executor resolves the
    # session and rejects a thread owned by another principal before saving.
    "design_proposal",
}


def test_every_executor_kind_has_a_scope_decision():
    """Adding an executor forces a row in the table or a line in the exempt set."""
    ungated = set(se._EXECUTORS) - set(se._KIND_SCOPE_RULES)
    assert ungated == _INTENTIONALLY_UNGATED, (
        "new staging kind(s) without a workspace-scope decision: "
        f"{sorted(ungated - _INTENTIONALLY_UNGATED)}"
    )


# ---------------------------------------------------------------------------
# Executors that BIND the workspace rather than gating a payload id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_app_binds_the_active_scope_over_the_payload(scoped, monkeypatch):
    """A stager-supplied ``workspace_id`` must not beat the bound scope.

    ``create_app`` carries no gateable target id (it is in the exempt set), so
    the executor's own binding IS the boundary — reading the payload first let
    a card staged in WS create the app in any workspace the caller could reach.
    """
    seen: Dict[str, Any] = {}

    async def fake_call(handler, user_id, **kwargs):
        seen.update(kwargs)
        return {"app": {"id": "a-new"}}

    monkeypatch.setattr(se, "_call_endpoint", fake_call)
    await se._x_create_app("u1", {"name": "X", "workspace_id": "n.Workspace.foreign"})
    assert seen["workspace_id"] == WS


@pytest.mark.asyncio
async def test_create_track_standalone_binds_the_active_scope_over_the_payload(
    scoped, monkeypatch
):
    """Same binding rule on ``create_track``'s standalone (no ``app_id``) path."""
    seen: Dict[str, Any] = {}

    async def fake_call(handler, user_id, **kwargs):
        seen.update(kwargs)
        return {"track": {"id": "t-new"}}

    monkeypatch.setattr(se, "_call_endpoint", fake_call)
    await se._x_create_track(
        "u1", {"title": "T", "workspace_id": "n.Workspace.foreign"}
    )
    assert seen["workspace_id"] == WS
