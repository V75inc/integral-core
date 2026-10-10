"""Shared workspace fixtures.

Six test modules had each grown their own copy of this helper. They drifted in
the only way that mattered: none of them wired an owner, so every one built an
org workspace that production cannot produce — and app install into such a
workspace used to yield an App with no ``OWNS`` edge, which no human can
administer (org staff resolve only to the implicit ``commenter`` — raised
from ``viewer`` for participation, still below every can_admin_* gate).
Install now refuses to do that, so the owner edge is part of the shape.
"""

from __future__ import annotations

from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User, Workspace
from app.services.app_graph import catalog_user, catalog_workspace
from app.utils.time import utc_now_iso


async def make_org_workspace(
    name: str = "WS Test", *, owner: User | None = None, **overrides
) -> Workspace:
    """Organization workspace with an owner, as production always has one.

    ``owner`` selects the single canonical principal; omitted creates one.
    ``overrides`` passes through to ``Workspace.create`` for the few callers
    that need a different shape.
    """
    now = utc_now_iso()
    fields = {
        "kind": "organization",
        "workspace_type": "company",
        "name": name,
        "name_fold": name.casefold(),
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    ws = await Workspace.create(**fields)
    await catalog_workspace(ws)
    owner = owner or await User.create(display_name=f"{name} owner")
    await catalog_user(owner)
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    return ws
