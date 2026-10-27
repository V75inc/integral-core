"""Cascade delete for apps: contained tracks, entries, and operational models.

Also removes the App's side-car nodes that hang off it via named edges —
``Dashboards``/``Dashboard`` (CONTAINS → CATALOGS), ``ShareLink``
(HAS_SHARE_LINK) and ``Invitation`` (INVITED_TO, incoming) — so an
uninstall leaves no dangling rows. Tracks and Entries get the same
side-car treatment via :func:`delete_resource_side_cars`.
"""

from __future__ import annotations

import logging
from typing import Any, List, Tuple

from jvspatial.core import Edge, Node, Walker
from jvspatial.core.context import get_default_context
from jvspatial.core.decorators import on_visit

from app.agentive.services.skill_registry import unregister_skills_for_app
from app.exceptions import AuthorizationUnavailableError
from app.models.access_delegation import HAS_ACTION_BINDING
from app.models.access_lifecycle import HAS_OWNERSHIP_PROPOSAL
from app.models.edges import (
    CATALOGS,
    CONTAINS,
    DEFINES_TRACK_PROFILE,
    HAS_APPLICATION_DEFINITION,
    HAS_OPERATIONAL_MODEL,
    HAS_SHARE_LINK,
    INVITED_TO,
    TEMPLATED_FROM,
    USES_TEMPLATE,
)
from app.models.nodes import (
    App,
    EntryType,
    OperationalModel,
    Tag,
    Track,
    View,
    Views,
    Workspace,
)
from app.services.app_graph import (
    get_track_attached_operational_model,
)

logger = logging.getLogger(__name__)


async def _disconnect_uses_template_to(template_id: str) -> None:
    tpl = await OperationalModel.get(template_id)
    if not tpl:
        return
    ctx = await tpl.get_context()
    rows = await ctx.database.find(
        "edge", {"target": template_id, "entity": "USES_TEMPLATE"}
    )
    if not rows:
        return

    async def _del(row: dict) -> None:
        edge = await USES_TEMPLATE.get(row["id"])
        if edge:
            await edge.delete()

    for row in rows:
        await _del(row)


async def _safe_delete_node(node, label: str) -> None:
    """Propagate every failure; verify the actual row disappeared before success."""
    context = get_default_context()
    await node.set_context(context)
    await node.delete(cascade=False)
    if await context.database.get("node", node.id) is not None:
        raise AuthorizationUnavailableError()


async def _named_children(node, edges: list, *, direction: str = "out") -> list:
    """Compare raw relationship candidates with bounded hydration before deletion."""
    context = await node.get_context()
    field = "source" if direction == "out" else "target"
    other = "target" if direction == "out" else "source"
    rows = await context.database.find(
        "edge",
        {field: node.id, "entity": {"$in": [edge._entity_name() for edge in edges]}},
        limit=10001,
    )
    if len(rows) > 10000 or any(
        not isinstance(row.get(other), str) or not row[other] for row in rows
    ):
        raise AuthorizationUnavailableError()
    children = await node.nodes(edge=edges, direction=direction, limit=10001)
    if {child.id for child in children} != {row[other] for row in rows}:
        raise AuthorizationUnavailableError()
    return list(children)


async def delete_resource_side_cars(node) -> None:
    """Delete all rooted consent/source/declaration sidecars and incoming invitations."""
    children = await _named_children(
        node,
        [
            HAS_SHARE_LINK,
            HAS_OWNERSHIP_PROPOSAL,
            HAS_ACTION_BINDING,
            HAS_APPLICATION_DEFINITION,
        ],
    )
    invites = await _named_children(node, [INVITED_TO], direction="in")
    for child in [*children, *invites]:
        await _safe_delete_node(child, "resource sidecar")


async def _delete_dashboards(app_node: App) -> None:
    """Delete every declared Dashboard subtree and registry, propagating errors."""
    registries = await app_node.nodes(
        edge=[CONTAINS], node=["Dashboards"], direction="out", limit=10001
    )
    if len(registries) > 10000:
        raise AuthorizationUnavailableError()
    for registry in registries:
        for dashboard in await _named_children(registry, [CATALOGS]):
            await _safe_delete_node(dashboard, "dashboard")
        await _safe_delete_node(registry, "dashboard registry")


async def _delete_app_side_cars(app_node: App) -> None:
    """Complete rooted dashboard/source/consent/declaration teardown."""
    from app.services.access_delegation import delete_app_delegation_roots

    await delete_app_delegation_roots(app_node.id)
    await _delete_dashboards(app_node)
    await delete_resource_side_cars(app_node)


async def _delete_views_registry(vreg: Views) -> None:
    """Delete saved Views with their registry in the caller's complete unit."""
    for view in await _named_children(vreg, [CATALOGS]):
        await _safe_delete_node(view, "view")
    await _safe_delete_node(vreg, "views registry")


class ModelDeletionInventory(Walker):
    """Collect the bounded named schema subtree before any destructive effect."""

    collected: dict[str, Any] = {}

    @on_visit(Node)
    async def collect(self, here: Node) -> None:
        if here.id in self.collected:
            return
        if not isinstance(here, (OperationalModel, Views, View, EntryType, Tag)):
            raise AuthorizationUnavailableError()
        if len(self.collected) >= 10000:
            raise AuthorizationUnavailableError()
        self.collected[here.id] = here
        if isinstance(here, OperationalModel):
            from app.models.edges import HAS_DRAFT_PROFILE

            children = await _named_children(
                here, [DEFINES_TRACK_PROFILE, HAS_DRAFT_PROFILE, CONTAINS]
            )
            extra_views = await here.nodes(
                edge=[Edge], node=["Views"], direction="out", limit=10001
            )
            if len(extra_views) > 10000:
                raise AuthorizationUnavailableError()
            await self.visit([*children, *extra_views])
        elif isinstance(here, Views):
            await self.visit(await _named_children(here, [CATALOGS]))


async def delete_operational_model_subtree(
    cp: OperationalModel,
    *,
    allowed_consumer_ids: frozenset[str] = frozenset(),
    additional_roots: tuple[OperationalModel, ...] = (),
) -> None:
    """Delete collected schema descendants in one complete caller-owned unit."""
    inventory = ModelDeletionInventory()
    for root in (cp, *additional_roots):
        await inventory.spawn(root)
    if any(
        isinstance(item, dict) and item.get("hook_error")
        for item in await inventory.get_report()
    ):
        raise AuthorizationUnavailableError()
    from app.api.errors import ResourceConflictError
    from app.models.edges import (
        HAS_DRAFT_PROFILE,
        HAS_LIBRARY_MODEL,
        HAS_VIEW,
        IS_OF_TYPE,
        TAGGED_WITH,
    )

    identities = set(inventory.collected)
    consumers = await get_default_context().database.find(
        "edge",
        {
            "target": {"$in": sorted(identities)},
            "entity": {
                "$in": [
                    family._entity_name()
                    for family in (
                        HAS_OPERATIONAL_MODEL,
                        HAS_DRAFT_PROFILE,
                        HAS_LIBRARY_MODEL,
                        HAS_VIEW,
                        IS_OF_TYPE,
                        TAGGED_WITH,
                        DEFINES_TRACK_PROFILE,
                        CONTAINS,
                    )
                ]
            },
        },
        limit=10001,
    )
    if len(consumers) > 10000:
        raise AuthorizationUnavailableError()
    if any(
        row.get("source") not in identities | set(allowed_consumer_ids)
        for row in consumers
    ):
        raise ResourceConflictError(
            message="Model schema has retained external consumers"
        )
    for node in reversed(list(inventory.collected.values())):
        if isinstance(node, OperationalModel):
            await _disconnect_uses_template_to(node.id)
        await _safe_delete_node(node, "model schema")


async def _delete_app_models(app_node: App) -> None:
    """Capture both attached and directly contained template models as one subtree."""
    roots = [
        node
        for node in await _named_children(app_node, [HAS_OPERATIONAL_MODEL, CONTAINS])
        if isinstance(node, OperationalModel)
    ]
    if roots:
        await delete_operational_model_subtree(
            roots[0],
            additional_roots=tuple(roots[1:]),
            allowed_consumer_ids=frozenset({app_node.id}),
        )


async def _apps_containing_track(track: Track) -> List[App]:
    # A missing/unhydratable parent must not turn a shared Track into an
    # exclusively owned subtree. Use the same complete parent set for cascade
    # admission and the later delete-versus-unlink decision.
    parents = await _named_children(track, [CONTAINS], direction="in")
    if any(
        not isinstance(parent, (App, Workspace))
        or (isinstance(parent, Workspace) and parent.id != track.workspace_id)
        for parent in parents
    ):
        raise AuthorizationUnavailableError()
    apps = [parent for parent in parents if isinstance(parent, App)]
    if any(parent.workspace_id != track.workspace_id for parent in apps):
        raise AuthorizationUnavailableError()
    return apps


async def _other_tracks_sharing_operational_model(
    cp: OperationalModel, exclude_track_id: str
) -> bool:
    """True if any Track other than ``exclude_track_id`` still holds a
    ``HAS_OPERATIONAL_MODEL`` edge to ``cp``.

    Anchor-provisioned template CPs are shared **by reference** across every
    Track anchored from the same ``(app_id, template_key)`` — see
    ``materialize_anchor_track``/``_resolve_or_create_template_operational_model``
    in ``operational_model_graph.py``. Deleting one referencing Track's CP
    subtree unconditionally would silently orphan every sibling Track that
    still points at the same CP by scalar ``attached_operational_model_id`` +
    edge, since nothing re-validates that reference afterward (confirmed
    live: an anchor track deletion here deleted a template CP that two
    still-active sibling Tracks depended on, breaking their Views with no
    error at delete time — only a downstream "View not found" much later).
    """
    referrers = await cp.nodes(
        edge=[HAS_OPERATIONAL_MODEL], direction="in", node=["Track"]
    )
    from app.models.edges import HAS_DRAFT_PROFILE, HAS_LIBRARY_MODEL

    retained_roots = await _named_children(
        cp,
        [DEFINES_TRACK_PROFILE, HAS_DRAFT_PROFILE, HAS_LIBRARY_MODEL, CONTAINS],
        direction="in",
    )
    return bool(retained_roots) or any(
        getattr(t, "id", "") != exclude_track_id for t in referrers
    )


async def delete_track_and_nested_content(
    track: Track, *, actor_user_id: str = ""
) -> None:
    """Enter a named current actor cascade; no implicit empty-principal authority."""
    from app.services.resource_cascade import resource_cascade_scope

    async with resource_cascade_scope(
        actor_id=actor_user_id,
        resource_type="track",
        resource_id=track.id,
        workspace_id=track.workspace_id,
    ):
        current = await Track.find_one({"id": track.id})
        if current is None:
            raise AuthorizationUnavailableError()
        from app.agentive.services.staging_execution import (
            capture_staging_change_event,
            staged_command_effect,
        )

        async with staged_command_effect(
            user_id=actor_user_id,
            workspace_id=current.workspace_id,
            kind="delete_track",
            targets={"track_id": current.id},
        ) as complete:
            before = await current.export(flat=True) if complete is not None else None
            await _delete_track_admitted(current, actor_user_id=actor_user_id)
            if complete is not None:
                await capture_staging_change_event(
                    {
                        "actor_kind": "human",
                        "actor_id": actor_user_id,
                        "action": "track.delete",
                        "resource_type": "Track",
                        "resource_id": current.id,
                        "before": before,
                        "after": None,
                        "scope": f"track:{current.id}",
                    }
                )
                await complete({"track_id": current.id, "deleted": True})


async def _delete_track_admitted(track: Track, *, actor_user_id: str) -> None:
    """Delete a track, its entries, and its attached operational model subtree.

    A track's attached OperationalModel is only cascade-deleted when this is
    the LAST Track referencing it (see ``_other_tracks_sharing_operational_model``
    — anchor-template CPs are shared by reference across sibling Tracks).
    Otherwise we merely unlink this track's ``HAS_OPERATIONAL_MODEL``/
    ``TEMPLATED_FROM`` edges and leave the shared CP intact for the other
    referencing Tracks.
    """
    from app.services.entry_deletion import delete_entry_fast

    entries = await track.nodes(
        edge=[CONTAINS], node=["Entry"], direction="out", limit=10001
    )
    if len(entries) > 10000:
        raise AuthorizationUnavailableError()
    for entry in entries:
        await delete_entry_fast(entry, actor_user_id=actor_user_id)
        context = await track.get_context()
        if await context.database.get("node", entry.id) is not None:
            raise AuthorizationUnavailableError()

    tcp = await get_track_attached_operational_model(track)
    if tcp:
        if await _other_tracks_sharing_operational_model(tcp, track.id):
            logger.info(
                "operational model %s still referenced by other tracks; "
                "unlinking track %s instead of deleting the shared CP",
                tcp.id,
                track.id,
            )
            ctx = await track.get_context()
            edges = await ctx.find_edges_between(
                track.id, tcp.id, edge_class=HAS_OPERATIONAL_MODEL
            )
            if edges:
                for edge in edges:
                    await edge.delete()
            templated_edges = await ctx.find_edges_between(
                track.id, tcp.id, edge_class=TEMPLATED_FROM
            )
            if templated_edges:
                for edge in templated_edges:
                    await edge.delete()
        else:
            await delete_operational_model_subtree(
                tcp, allowed_consumer_ids=frozenset({track.id})
            )

    await delete_resource_side_cars(track)

    await _safe_delete_node(track, "track")


async def delete_app_cascade(
    app_node: App, *, actor_user_id: str = ""
) -> Tuple[int, int]:
    """Admit a named App subtree under its current actor and workspace fence."""
    from app.services.resource_cascade import resource_cascade_scope

    async with resource_cascade_scope(
        actor_id=actor_user_id,
        resource_type="app",
        resource_id=app_node.id,
        workspace_id=app_node.workspace_id,
    ):
        current = await App.find_one({"id": app_node.id})
        if current is None:
            raise AuthorizationUnavailableError()
        from app.agentive.services.staging_execution import (
            capture_staging_change_event,
            staged_command_effect,
        )
        from app.services.app_lifecycle import _resolve_bundle_slug
        from app.services.bundle_cleanup_outbox import retain_bundle_cleanup

        async with staged_command_effect(
            user_id=actor_user_id,
            workspace_id=current.workspace_id,
            kind="delete_app",
            targets={"app_id": current.id},
        ) as complete:
            before = await current.export(flat=True) if complete is not None else None
            slug = await _resolve_bundle_slug(current)
            await retain_bundle_cleanup(
                app_id=current.id, workspace_id=current.workspace_id, bundle_slug=slug
            )
            result = await _delete_app_admitted(current, actor_user_id=actor_user_id)
            if complete is not None:
                await capture_staging_change_event(
                    {
                        "actor_kind": "human",
                        "actor_id": actor_user_id,
                        "action": "app.delete",
                        "resource_type": "App",
                        "resource_id": current.id,
                        "before": before,
                        "after": None,
                        "scope": f"app:{current.id}",
                    }
                )
                await complete(
                    {
                        "app_id": current.id,
                        "deleted": True,
                        "deleted_tracks": result[0],
                        "unlinked_tracks": result[1],
                    }
                )
            return result


async def _delete_app_admitted(app_node: App, *, actor_user_id: str) -> Tuple[int, int]:
    """Remove the App and contained data.

    Tracks that appear in more than one app are only unlinked from this app_node.
    Any Skill nodes wired to the App (bundle-origin or app-scoped
    workspace-origin) are destroyed — an App's accompanying skill's lifecycle
    is bound to the App. Dashboards, ShareLinks and Invitations attached to
    the App are removed as well.

    Returns:
        (deleted_track_count, unlinked_track_count)
    """
    tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"])
    track_list = list(tracks)

    if not track_list:
        await _delete_app_models(app_node)
        await unregister_skills_for_app(app_node.id, strict=True)
        await _delete_app_side_cars(app_node)
        await _safe_delete_node(app_node, "app")
        return 0, 0

    ctx = await app_node.get_context()

    parent_results = [await _apps_containing_track(track) for track in track_list]

    to_delete: List[Track] = []
    to_unlink: List[Track] = []
    for track, parents in zip(track_list, parent_results):
        parent_ids = {p.id for p in parents}
        if parent_ids == {app_node.id}:
            to_delete.append(track)
        else:
            to_unlink.append(track)

    if to_delete:
        for track in to_delete:
            await delete_track_and_nested_content(track, actor_user_id=actor_user_id)

    if to_unlink:

        async def _unlink(t: Track) -> None:
            edges = await ctx.find_edges_between(app_node.id, t.id, edge_class=CONTAINS)
            if edges:
                for edge in edges:
                    await edge.delete()

        for track in to_unlink:
            await _unlink(track)

    await _delete_app_models(app_node)

    await unregister_skills_for_app(app_node.id, strict=True)
    await _delete_app_side_cars(app_node)
    await _safe_delete_node(app_node, "app")
    return len(to_delete), len(to_unlink)
