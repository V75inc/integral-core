"""Workspace lifecycle helpers: cascade-aware delete.

Two delete modes:

* **unlink** (default) — disassociate contained Apps and Tracks by
  clearing ``workspace_id`` and removing their branch-registry CATALOGS
  edge.  The Apps/Tracks themselves are preserved as user-owned.
* **cascade** — fully delete contained Apps (via
  ``purge_app_with_bundle_teardown``, which wraps ``delete_app_cascade``
  with the bundle hook/tool unregister) and standalone Tracks (via
  ``delete_track_and_nested_content``).  Tracks contained by an App
  are handled by the App cascade.  ChatThreads in the workspace's
  branch registry are deleted in both modes (no unlink semantics — a
  thread without a workspace would have nowhere to live).
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Tuple, Type

from jvspatial.core import Node

from app.models.edges import CATALOGS
from app.models.nodes import (
    Apps,
    ChatThread,
    ChatThreads,
    Tracks,
    Workspace,
)
from app.services.app_deletion import delete_track_and_nested_content

logger = logging.getLogger(__name__)


async def _branch_for(workspace_id: str, kind: str):  # type: ignore[no-untyped-def]
    """Resolve the per-workspace branch registry by deterministic id."""
    from app.services.app_graph import _workspace_branch_id

    classes: dict[str, Type[Node]] = {
        "apps": Apps,
        "tracks": Tracks,
        "chat_threads": ChatThreads,
    }
    cls = classes[kind]
    return await cls.get(_workspace_branch_id(workspace_id, kind))


async def _collect_contained(workspace: Workspace, *, kind: str) -> List:
    """Enumerate a workspace's Apps or Tracks via its branch registry."""
    # jvspatial ``node=`` filters match ``__entity_name__`` — "WorkspaceApp"
    # for App (see app/models/nodes.py), which is what every other call site
    # in the codebase passes. Filtering on "App" matched NOTHING, so this
    # helper returned an empty list and ``delete_workspace_cascade`` silently
    # neither cascaded nor unlinked any App.
    target_node = {"apps": "WorkspaceApp", "tracks": "Track"}[kind]
    branch = await _branch_for(workspace.id, kind)
    if branch is None:
        return []
    return list(await branch.nodes(edge=[CATALOGS], node=[target_node]))


async def _collect_chat_threads(workspace: Workspace) -> List[ChatThread]:
    branch = await _branch_for(workspace.id, "chat_threads")
    if branch is None:
        return []
    return list(await branch.nodes(edge=[CATALOGS], node=["ChatThread"]))


async def _unlink_node(workspace: Workspace, node) -> None:  # type: ignore[type-arg]
    """Remove ``node`` from its workspace branch registry and clear the field."""
    ctx = await workspace.get_context()
    for kind in ("apps", "tracks"):
        branch = await _branch_for(workspace.id, kind)
        if branch is None:
            continue
        catalog_edges = await ctx.find_edges_between(
            branch.id, node.id, edge_class=CATALOGS
        )
        if catalog_edges:
            await asyncio.gather(*(e.delete() for e in catalog_edges))
    if getattr(node, "workspace_id", None):
        node.workspace_id = ""
        try:
            await node.save()
        except Exception:
            logger.exception("clear workspace_id on %s", getattr(node, "id", ""))


async def _delete_branches(workspace: Workspace) -> None:
    """Delete the three per-workspace branch registry nodes."""
    for kind in ("apps", "tracks", "chat_threads"):
        branch = await _branch_for(workspace.id, kind)
        if branch is None:
            continue
        try:
            await branch.delete()
        except Exception:
            logger.exception("delete branch %s for workspace %s", kind, workspace.id)


async def delete_workspace_cascade(
    workspace: Workspace, *, cascade: bool = False
) -> Tuple[int, int, int, int]:
    """Delete a Workspace with either unlink (default) or cascade semantics.

    Returns ``(unlinked_spaces, unlinked_tracks, deleted_spaces, deleted_tracks)``.
    Tracks-inside-deleted-apps are counted under the App cascade and not
    in ``deleted_tracks`` (which counts only standalone tracks). ChatThreads
    in the workspace are always deleted regardless of mode.
    """
    apps = await _collect_contained(workspace, kind="apps")
    tracks = await _collect_contained(workspace, kind="tracks")
    threads = await _collect_chat_threads(workspace)

    unlinked_spaces = unlinked_tracks = 0
    deleted_spaces = deleted_tracks = 0

    if cascade:
        if apps:
            # Not ``delete_app_cascade`` directly — a deleted App whose bundle
            # registered hooks/tools would keep dispatching from the
            # in-process registry until restart. ``purge_app_with_bundle_teardown``
            # resolves the bundle slug BEFORE the cascade strips the attached
            # OperationalModel, then unregisters.
            from app.services.app_lifecycle import purge_app_with_bundle_teardown

            await asyncio.gather(*(purge_app_with_bundle_teardown(sp) for sp in apps))
            deleted_spaces = len(apps)
        if tracks:
            await asyncio.gather(*(delete_track_and_nested_content(t) for t in tracks))
            deleted_tracks = len(tracks)
    else:
        if apps:
            await asyncio.gather(*(_unlink_node(workspace, sp) for sp in apps))
            unlinked_spaces = len(apps)
        if tracks:
            await asyncio.gather(*(_unlink_node(workspace, t) for t in tracks))
            unlinked_tracks = len(tracks)

    if threads:
        await asyncio.gather(*(t.delete() for t in threads))

    await _delete_branches(workspace)

    try:
        await workspace.delete()
    except Exception:
        logger.exception("delete workspace %s", getattr(workspace, "id", ""))

    return unlinked_spaces, unlinked_tracks, deleted_spaces, deleted_tracks
