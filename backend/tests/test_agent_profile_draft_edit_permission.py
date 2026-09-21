"""Regression: a draft OperationalModel must resolve edit rights via its
published parent.

A draft CP carries the parent's scope (``track``/``app``) but is attached to
no Track/App — only the published parent holds the
``attached_operational_model_id`` back-pointer. Gating edit permission on the
draft node's own id alone found no owner and denied a user who plainly owns the
underlying track — the bug that made ``integral_get_model_draft`` return
"no edit access" for a workspace owner. ``_user_can_edit_cp`` must fall back to
the parent (``draft_of_id``) when the node itself is unattached.
"""

from __future__ import annotations

import pytest

from app.services import operational_model_authoring


class _CP:
    library_package = False

    def __init__(self, cp_id: str, scope: str, draft_of_id=None):
        self.id = cp_id
        self.scope = scope
        self.draft_of_id = draft_of_id


@pytest.mark.asyncio
async def test_draft_cp_edit_resolves_via_published_parent(monkeypatch):
    """A track-scope draft (attached to no Track) is editable when the caller
    can edit the track owning its published parent."""
    parent_id = "n.OperationalModel.parent"
    track_id = "n.Track.owning"

    async def fake_track_find(query):
        # Only the published parent carries the attachment back-pointer.
        attached = query.get("context.attached_operational_model_id")
        if attached == parent_id:
            return [type("T", (), {"id": track_id})()]
        return []

    async def fake_can_edit_track(user_id, tid):
        return tid == track_id

    monkeypatch.setattr("app.models.nodes.Track.find", staticmethod(fake_track_find))
    monkeypatch.setattr("app.services.permissions.can_edit_track", fake_can_edit_track)

    draft = _CP("n.OperationalModel.draft", "track", draft_of_id=parent_id)
    assert (
        await operational_model_authoring._user_can_edit_cp(user_id="u1", cp=draft)
        is True
    )


@pytest.mark.asyncio
async def test_orphan_cp_without_parent_is_denied(monkeypatch):
    """A track-scope CP attached to nothing AND with no parent stays denied."""

    async def fake_track_find(query):
        return []

    async def fake_can_edit_track(user_id, tid):
        return True

    monkeypatch.setattr("app.models.nodes.Track.find", staticmethod(fake_track_find))
    monkeypatch.setattr("app.services.permissions.can_edit_track", fake_can_edit_track)

    orphan = _CP("n.OperationalModel.orphan", "track", draft_of_id=None)
    assert (
        await operational_model_authoring._user_can_edit_cp(user_id="u1", cp=orphan)
        is False
    )
