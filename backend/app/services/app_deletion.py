"""Cascade delete for apps: contained tracks, entries, and operational models.

Also removes the App's side-car nodes that hang off it via named edges —
``Dashboards``/``Dashboard`` (CONTAINS → CATALOGS), ``ShareLink``
(HAS_SHARE_LINK) and ``Invitation`` (INVITED_TO, incoming) — so an
uninstall leaves no dangling rows. Tracks and Entries get the same
side-car treatment via :func:`delete_resource_side_cars`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Tuple

from jvspatial.core import Edge

from app.agentive.services.skill_registry import unregister_skills_for_app
from app.models.edges import (
    CATALOGS,
    CONTAINS,
    DEFINES_TRACK_PROFILE,
    HAS_OPERATIONAL_MODEL,
    HAS_SHARE_LINK,
    INVITED_TO,
    TEMPLATED_FROM,
    USES_TEMPLATE,
)
from app.models.nodes import App, Dashboards, OperationalModel, Track, Views
from app.services.app_graph import (
    get_app_attached_operational_model,
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

    await asyncio.gather(*(_del(r) for r in rows))


async def _safe_delete_node(node, label: str) -> None:  # type: ignore[type-arg]
    """``node.delete(cascade=False)`` that logs instead of raising."""
    try:
        await node.delete(cascade=False)
    except Exception:
        logger.exception("delete %s %s", label, getattr(node, "id", ""))


async def delete_resource_side_cars(node) -> None:  # type: ignore[type-arg]
    """Remove ShareLink + Invitation nodes attached to an App / Track / Entry.

    * ``ShareLink`` — ``resource —HAS_SHARE_LINK→ ShareLink`` (outgoing).
    * ``Invitation`` — ``Invitation —INVITED_TO→ resource`` (incoming). The
      Invitation is also cataloged under the ``Invitations`` registry;
      ``cascade=False`` drops that CATALOGS edge along with the node.

    Best-effort: enumeration or delete failures are logged, never raised,
    so the resource delete itself always proceeds.
    """
    try:
        links = await node.nodes(edge=[HAS_SHARE_LINK], node=["ShareLink"])
    except Exception:
        logger.exception("enumerate share links for %s", getattr(node, "id", ""))
        links = []
    try:
        invites = await node.nodes(
            edge=[INVITED_TO], direction="in", node=["Invitation"]
        )
    except Exception:
        logger.exception("enumerate invitations for %s", getattr(node, "id", ""))
        invites = []
    deletions = [_safe_delete_node(ln, "share link") for ln in links]
    deletions.extend(_safe_delete_node(inv, "invitation") for inv in invites)
    if deletions:
        await asyncio.gather(*deletions)


async def _delete_dashboards(app_node: App) -> None:
    """Delete every Dashboard + the Dashboards registry cataloged under ``app_node``."""
    try:
        registries = await app_node.nodes(edge=[CONTAINS], node=["Dashboards"])
    except Exception:
        logger.exception("enumerate dashboards registry for %s", app_node.id)
        return

    async def _one(dreg: Dashboards) -> None:
        try:
            dashboards = await dreg.nodes(edge=[CATALOGS], node=["Dashboard"])
        except Exception:
            logger.exception("enumerate dashboards for %s", getattr(dreg, "id", ""))
            dashboards = []
        if dashboards:
            await asyncio.gather(
                *(_safe_delete_node(d, "dashboard") for d in dashboards)
            )
        await _safe_delete_node(dreg, "dashboards registry")

    if registries:
        await asyncio.gather(*(_one(r) for r in registries))  # type: ignore[arg-type]


async def _delete_app_side_cars(app_node: App) -> None:
    """Dashboards, ShareLinks and Invitations attached to the App."""
    await _delete_dashboards(app_node)
    await delete_resource_side_cars(app_node)


async def _delete_views_registry(vreg: Views) -> None:
    views = await vreg.nodes(edge=[CATALOGS], node=["View"])
    if views:

        async def _del(v) -> None:  # type: ignore[type-arg]
            try:
                await v.delete(cascade=False)
            except Exception:
                logger.exception("delete view %s", getattr(v, "id", ""))

        await asyncio.gather(*(_del(v) for v in views))

    try:
        await vreg.delete(cascade=False)
    except Exception:
        logger.exception("delete views registry %s", getattr(vreg, "id", ""))


async def delete_operational_model_subtree(cp: OperationalModel) -> None:
    """Delete a OperationalModel and graph nodes attached under it."""
    templates = await cp.nodes(edge=[DEFINES_TRACK_PROFILE], node=["OperationalModel"])
    if templates:
        await asyncio.gather(
            *(delete_operational_model_subtree(tpl) for tpl in templates)
        )

    await _disconnect_uses_template_to(cp.id)

    vregs = await cp.nodes(edge=[Edge], node=["Views"])
    if vregs:
        await asyncio.gather(
            *(_delete_views_registry(vreg) for vreg in vregs)  # type: ignore[arg-type]
        )

    entry_types = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    tags = await cp.nodes(edge=[CONTAINS], node=["Tag"])

    async def _safe_del(node, label: str) -> None:  # type: ignore[type-arg]
        try:
            await node.delete(cascade=False)
        except Exception:
            logger.exception("delete %s %s", label, getattr(node, "id", ""))

    deletions = [_safe_del(et, "entry type") for et in entry_types]
    deletions.extend(_safe_del(tag, "tag") for tag in tags)
    if deletions:
        await asyncio.gather(*deletions)

    try:
        await cp.delete(cascade=False)
    except Exception:
        logger.exception("delete operational model %s", getattr(cp, "id", ""))


async def _apps_containing_track(track: Track) -> List[App]:
    parents = await track.nodes(edge=[CONTAINS], direction="in", node=["WorkspaceApp"])
    return list(parents)


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
    return any(getattr(t, "id", "") != exclude_track_id for t in referrers)


async def delete_track_and_nested_content(track: Track) -> None:
    """Delete a track, its entries, and its attached operational model subtree.

    A track's attached OperationalModel is only cascade-deleted when this is
    the LAST Track referencing it (see ``_other_tracks_sharing_operational_model``
    — anchor-template CPs are shared by reference across sibling Tracks).
    Otherwise we merely unlink this track's ``HAS_OPERATIONAL_MODEL``/
    ``TEMPLATED_FROM`` edges and leave the shared CP intact for the other
    referencing Tracks.
    """
    from app.services.entry_deletion import delete_entry_fast

    entries = await track.nodes(edge=[CONTAINS], node=["Entry"])
    if entries:

        async def _del_entry(ent) -> None:  # type: ignore[type-arg]
            try:
                await delete_entry_fast(ent)
            except Exception:
                logger.exception("delete entry %s", getattr(ent, "id", ""))

        await asyncio.gather(*(_del_entry(e) for e in entries))

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
                await asyncio.gather(*(e.delete() for e in edges))
            templated_edges = await ctx.find_edges_between(
                track.id, tcp.id, edge_class=TEMPLATED_FROM
            )
            if templated_edges:
                await asyncio.gather(*(e.delete() for e in templated_edges))
        else:
            await delete_operational_model_subtree(tcp)

    await delete_resource_side_cars(track)

    try:
        await track.delete(cascade=False)
    except Exception:
        logger.exception("delete track %s", getattr(track, "id", ""))


async def delete_app_cascade(app_node: App) -> Tuple[int, int]:
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
        sacp = await get_app_attached_operational_model(app_node)
        if sacp:
            await delete_operational_model_subtree(sacp)
        await unregister_skills_for_app(app_node.id)
        await _delete_app_side_cars(app_node)
        await app_node.delete(cascade=False)
        return 0, 0

    ctx = await app_node.get_context()

    parent_results = await asyncio.gather(
        *(_apps_containing_track(t) for t in track_list)
    )

    to_delete: List[Track] = []
    to_unlink: List[Track] = []
    for track, parents in zip(track_list, parent_results):
        parent_ids = {p.id for p in parents}
        if parent_ids == {app_node.id}:
            to_delete.append(track)
        else:
            to_unlink.append(track)

    if to_delete:
        await asyncio.gather(*(delete_track_and_nested_content(t) for t in to_delete))

    if to_unlink:

        async def _unlink(t: Track) -> None:
            edges = await ctx.find_edges_between(app_node.id, t.id, edge_class=CONTAINS)
            if edges:
                await asyncio.gather(*(e.delete() for e in edges))

        await asyncio.gather(*(_unlink(t) for t in to_unlink))

    sacp = await get_app_attached_operational_model(app_node)
    if sacp:
        await delete_operational_model_subtree(sacp)

    await unregister_skills_for_app(app_node.id)
    await _delete_app_side_cars(app_node)
    await app_node.delete(cascade=False)
    return len(to_delete), len(to_unlink)
