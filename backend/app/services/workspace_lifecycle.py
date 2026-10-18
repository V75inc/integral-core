"""Owner-only rooted workspace deletion; rehome requires explicit destination.

Nonempty implicit unlink is rejected. Explicit cascade deletes canonical data
and propagates all failures; PostgreSQL joins graph effects to one owner fence.
"""

from __future__ import annotations

import logging
from typing import List, Tuple, Type

from jvspatial.core import Node

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.models.edges import CATALOGS
from app.models.nodes import (
    Apps,
    ChatThread,
    ChatThreads,
    Tracks,
    Workspace,
)
from app.services.app_deletion import delete_track_and_nested_content
from app.services.membership_write_scope import membership_write_scope
from app.services.workspace_governance import owner_governance_allowed
from app.services.workspace_permissions import can_access_workspace

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
    return await cls.find_one({"id": _workspace_branch_id(workspace_id, kind)})


async def _collect_contained(workspace: Workspace, *, kind: str) -> List:
    """Enumerate a workspace's Apps or Tracks via its branch registry."""
    # jvspatial ``node=`` filters match ``__entity_name__`` — "WorkspaceApp"
    # for App (see app/models/nodes.py), which is what every other call site
    # in the codebase passes. Filtering on "App" matched NOTHING, so this
    # helper returned an empty list and ``delete_workspace_cascade`` silently
    # neither cascaded nor unlinked any App.
    from jvspatial.core.context import get_default_context

    from app.exceptions import AuthorizationUnavailableError
    from app.models.nodes import App, Track
    from app.services.app_deletion import _named_children
    from app.services.resource_location import KNOWN_APP_STATES, locate_resources

    model = {"apps": App, "tracks": Track}[kind]
    database = get_default_context().database
    primary = await database.find(
        "node",
        {"entity": model._entity_name(), "context.workspace_id": workspace.id},
        limit=10001,
    )
    if len(primary) > 10000:
        raise AuthorizationUnavailableError()
    branch = await _branch_for(workspace.id, kind)
    if branch is None:
        if primary:
            raise AuthorizationUnavailableError()
        return []
    rows = await _named_children(branch, [CATALOGS])
    if any(not isinstance(node, model) for node in rows) or {
        node.id for node in rows
    } != {row["id"] for row in primary}:
        raise AuthorizationUnavailableError()
    locations = await locate_resources(
        "app" if kind == "apps" else "track",
        [node.id for node in rows],
        allowed_app_states=KNOWN_APP_STATES,
        selected_records=primary,
        workspace_hints=[workspace.id],
    )
    for node in rows:
        location = locations.get(node.id)
        if location is None or location.workspace_id != workspace.id:
            raise AuthorizationUnavailableError()
    return rows


async def _collect_chat_threads(workspace: Workspace) -> List[ChatThread]:
    branch = await _branch_for(workspace.id, "chat_threads")
    if branch is None:
        return []
    return list(await branch.nodes(edge=[CATALOGS], node=["ChatThread"]))


async def _delete_branches(workspace: Workspace) -> None:
    """Delete the three per-workspace branch registry nodes."""
    for kind in ("apps", "tracks", "chat_threads"):
        branch = await _branch_for(workspace.id, kind)
        if branch is None:
            continue
        await branch.delete()


async def delete_workspace_cascade(
    workspace: Workspace, *, cascade: bool = False, actor_id: str = ""
) -> Tuple[int, int, int, int]:
    """Current owner-only deletion; nonempty implicit unlink is rejected."""
    from app.runtime.identity import identity_adapter

    await identity_adapter().preserve_workspace(workspace.id)
    if not actor_id:
        raise InsufficientPermissionsError(
            message="Explicit workspace owner identity required"
        )
    async with membership_write_scope(workspace.id, principal_ids=(actor_id,)):
        current = await Workspace.find_one({"id": workspace.id})
        if (
            current is None
            or await can_access_workspace(actor_id, workspace.id) != "owner"
            or not owner_governance_allowed()
        ):
            raise InsufficientPermissionsError(
                message="Only the workspace owner may delete"
            )
        from app.services.resource_cascade import resource_cascade_scope

        async with resource_cascade_scope(
            actor_id=actor_id,
            resource_type="workspace",
            resource_id=current.id,
            workspace_id=current.id,
        ):
            return await _delete_admitted_workspace(
                current, cascade=cascade, actor_id=actor_id
            )


async def _delete_admitted_workspace(
    workspace: Workspace, *, cascade: bool, actor_id: str
) -> Tuple[int, int, int, int]:
    """Delete a Workspace with either unlink (default) or cascade semantics.

    Returns ``(unlinked_spaces, unlinked_tracks, deleted_spaces, deleted_tracks)``.
    Tracks-inside-deleted-apps are counted under the App cascade and not
    in ``deleted_tracks`` (which counts only standalone tracks). ChatThreads
    in the workspace are always deleted regardless of mode.
    """
    apps = await _collect_contained(workspace, kind="apps")
    tracks = await _collect_contained(workspace, kind="tracks")
    from app.models.edges import CONTAINS

    standalone = []
    for track in tracks:
        parents = await track.nodes(
            edge=[CONTAINS], node=["WorkspaceApp"], direction="in", limit=2
        )
        if not parents:
            standalone.append(track)
    tracks = standalone
    threads = await _collect_chat_threads(workspace)
    from app.models.edges import HAS_AUTHORIZATION_STATE, HAS_LIBRARY_MODEL
    from app.models.nodes import WorkspaceAuthorizationState
    from app.services.access_delegation import (
        delete_workspace_delegation_roots,
        workspace_delegation_roots,
    )

    delegation_roots = await workspace_delegation_roots(workspace.id)
    library_models = await workspace.nodes(
        edge=[HAS_LIBRARY_MODEL],
        node=["OperationalModel"],
        direction="out",
        limit=10001,
    )
    if len(library_models) > 10000:
        from app.exceptions import AuthorizationUnavailableError

        raise AuthorizationUnavailableError()

    unlinked_spaces = unlinked_tracks = 0
    deleted_spaces = deleted_tracks = 0

    if not cascade and (apps or tracks or delegation_roots or library_models):
        raise ResourceConflictError(
            message="Nonempty workspace requires explicit destination rehome or cascade deletion"
        )
    if cascade:
        await delete_workspace_delegation_roots(workspace.id)
        if apps:
            # Not ``delete_app_cascade`` directly — a deleted App whose bundle
            # registered hooks/tools would keep dispatching from the
            # in-process registry until restart. ``purge_app_with_bundle_teardown``
            # resolves the bundle slug BEFORE the cascade strips the attached
            # OperationalModel, then unregisters.
            from app.services.app_lifecycle import purge_app_with_bundle_teardown

            for sp in apps:
                await purge_app_with_bundle_teardown(sp, actor_user_id=actor_id)
            deleted_spaces = len(apps)
        if tracks:
            for track in tracks:
                await delete_track_and_nested_content(track, actor_user_id=actor_id)
            deleted_tracks = len(tracks)
    if threads:
        from app.services.chat_threads import delete_thread_messages

        async def delete_thread(thread: ChatThread) -> None:
            await delete_thread_messages(thread)
            await thread.delete()

        for thread in threads:
            await delete_thread(thread)

    if cascade and library_models:
        from app.services.app_deletion import delete_operational_model_subtree

        for model in library_models:
            await delete_operational_model_subtree(
                model, allowed_consumer_ids=frozenset({workspace.id})
            )
    await _delete_branches(workspace)
    for state in await workspace.nodes(
        edge=[HAS_AUTHORIZATION_STATE],
        node=[WorkspaceAuthorizationState._entity_name()],
        direction="out",
        limit=2,
    ):
        await state.delete()
    from app.services.app_deletion import delete_resource_side_cars

    await delete_resource_side_cars(workspace)
    await workspace.delete()

    return unlinked_spaces, unlinked_tracks, deleted_spaces, deleted_tracks
