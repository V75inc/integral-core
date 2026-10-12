"""Explicit cross-workspace rehome with retained revocations and canonical roots."""

from contextlib import AsyncExitStack
from typing import Any

from jvspatial.core import Node, Walker
from jvspatial.core.context import get_default_context
from jvspatial.core.decorators import on_visit

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
)
from app.exceptions import AuthorizationUnavailableError
from app.models.access_lifecycle import RESOURCE_ACCESS_BLOCK
from app.models.edges import (
    CATALOGS,
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    HAS_LIBRARY_MODEL,
    HAS_OPERATIONAL_MODEL,
    HAS_VIEW,
    OWNS,
    REFERENCES,
)
from app.models.nodes import App, Entry, OperationalModel, Track, Workspace
from app.services.app_graph import catalog_app, catalog_track, ensure_workspace_branches
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.authorization_scope import authorization_scope
from app.services.grant_history import grant_baseline, record_grant_change
from app.services.identity_admission import identity_admission, resolve_current_profile
from app.services.permissions import get_user_node
from app.services.resource_location import DATA_APP_STATES, locate_resource
from app.services.workspace_permissions import can_access_workspace
from app.services.workspace_suspension import workspace_suspended
from app.utils.time import utc_now_iso


class RehomeInventory(Walker):
    """Follow the canonical data/schema subtree, withholding incomplete inventories."""

    resources: dict[str, Any] = {}

    @on_visit(Node)
    async def collect(self, here: Node) -> None:
        if here.id in self.resources:
            return
        self.resources[here.id] = here
        children = await here.nodes(
            edge=[CONTAINS, CATALOGS, HAS_OPERATIONAL_MODEL, HAS_VIEW],
            direction="out",
            limit=10001,
        )
        if len(children) > 10000 or len(self.resources) > 10000:
            raise AuthorizationUnavailableError()
        await self.visit(children)


async def rehome_workspace_resources(
    *, actor_id: str, source_id: str, destination_id: str
) -> dict:
    """Move into an explicitly owner-controlled destination, never minting guests.

    Apps pause; workspace-wide visibility becomes private. Foreign references,
    custom attached execution subsystems and external model consumers require
    reviewed migration and are refused before mutation.
    """
    if source_id == destination_id:
        raise BadRequestError(message="Destination must differ from source")
    from app.services.workspace_governance import owner_governance_allowed

    if not owner_governance_allowed():
        raise InsufficientPermissionsError(message="Protected owner authority required")
    database = get_default_context().database
    if not graph_transaction_available(database):
        raise AuthorizationUnavailableError()
    from app.services.workspace_lifecycle import _collect_contained

    preflight_source = await Workspace.find_one({"id": source_id})
    if preflight_source is None:
        raise AuthorizationUnavailableError()
    preflight_libraries = await preflight_source.nodes(
        edge=[HAS_LIBRARY_MODEL],
        node=["OperationalModel"],
        direction="out",
        limit=10001,
    )
    if len(preflight_libraries) > 10000:
        raise AuthorizationUnavailableError()
    preflight_roots = [
        *preflight_libraries,
        *await _collect_contained(preflight_source, kind="apps"),
        *await _collect_contained(preflight_source, kind="tracks"),
    ]
    preflight_walker = RehomeInventory()
    for resource in preflight_roots:
        await preflight_walker.spawn(resource)
    preflight_ids = sorted(preflight_walker.resources)
    preflight_grants = await database.find(
        "edge",
        {
            "target": {"$in": preflight_ids},
            "entity": {
                "$in": [
                    COLLABORATES_ON.__name__,
                    EXCLUDED_FROM.__name__,
                    OWNS.__name__,
                    RESOURCE_ACCESS_BLOCK.__name__,
                ]
            },
        },
        limit=10001,
    )
    if len(preflight_grants) > 10000:
        raise AuthorizationUnavailableError()
    participants = {actor_id, *(row["source"] for row in preflight_grants)}
    async with postgres_graph_transaction(database):
        async with identity_admission(participants, active_principal_ids=(actor_id,)):
            async with AsyncExitStack() as stack:
                revisions = {}
                for workspace_id in sorted((source_id, destination_id)):
                    revisions[workspace_id] = await stack.enter_async_context(
                        authorization_scope(workspace_id, authority_mutation=True)
                    )
                actor = await get_user_node(actor_id)
                if actor is None:
                    raise InsufficientPermissionsError(
                        message="Both workspaces require protected owner authority"
                    )
                for identity in (source_id, destination_id):
                    if await can_access_workspace(actor.id, identity) != "owner":
                        raise InsufficientPermissionsError(
                            message="Both workspaces require protected owner authority"
                        )
                source = await Workspace.find_one({"id": source_id})
                destination = await Workspace.find_one({"id": destination_id})
                if source is None or destination is None:
                    raise AuthorizationUnavailableError()

                apps = await _collect_contained(source, kind="apps")
                tracks = await _collect_contained(source, kind="tracks")
                libraries = await source.nodes(
                    edge=[HAS_LIBRARY_MODEL],
                    node=["OperationalModel"],
                    direction="out",
                    limit=10001,
                )
                from app.services.access_delegation import workspace_delegation_roots

                if await workspace_delegation_roots(source_id):
                    raise ResourceConflictError(
                        message="Group/action delegation requires reviewed migration before rehome"
                    )
                roots = [*apps, *tracks, *libraries]
                walker = RehomeInventory()
                for resource in roots:
                    await walker.spawn(resource)
                report = await walker.get_report()
                if any(
                    isinstance(item, dict) and item.get("hook_error") for item in report
                ):
                    raise AuthorizationUnavailableError()
                resources = walker.resources
                await _validate_rehome(resources, source_id)
                await ensure_workspace_branches(destination)
                context = get_default_context()
                removed = 0
                for resource in resources.values():
                    # Remove identified rights for users who lack destination
                    # admission rather than leaving latent grants to resurrect.
                    for family in (
                        COLLABORATES_ON,
                        EXCLUDED_FROM,
                        OWNS,
                        RESOURCE_ACCESS_BLOCK,
                    ):
                        rows = await context.database.find(
                            "edge",
                            {"target": resource.id, "entity": family.__name__},
                            limit=10001,
                        )
                        if len(rows) > 10000:
                            raise AuthorizationUnavailableError()
                        for row in rows:
                            if row["source"] not in participants:
                                raise ResourceConflictError(
                                    message="Resource grant inventory changed; retry reviewed rehome"
                                )
                            user = await resolve_current_profile(
                                row["source"], require_active=False
                            )
                            if user is None:
                                raise AuthorizationUnavailableError()
                            if family is RESOURCE_ACCESS_BLOCK:
                                from app.services.resource_access_blocks import _fact

                                _fact(
                                    row,
                                    user_id=user.id,
                                    resource_id=resource.id,
                                    workspace_id=source_id,
                                )
                                now = utc_now_iso()
                                updates = {
                                    "context.workspace_id": destination_id,
                                    "context.changed_at": now,
                                }
                                if "workspace_id" in row:
                                    updates["workspace_id"] = destination_id
                                if "changed_at" in row:
                                    updates["changed_at"] = now
                                if "revision" in row:
                                    updates["revision"] = row["context"]["revision"] + 1
                                updated = await context.database.find_one_and_update(
                                    "edge",
                                    {"id": row["id"]},
                                    {"$set": updates, "$inc": {"context.revision": 1}},
                                )
                                if updated is None:
                                    raise AuthorizationUnavailableError()
                                continue
                            if await can_access_workspace(
                                user.id, destination_id
                            ) != "none" and not await workspace_suspended(
                                user.id, source_id
                            ):
                                continue
                            if family in (COLLABORATES_ON, OWNS):
                                await grant_baseline(
                                    source_id=user.id,
                                    target_id=resource.id,
                                    grant_class=family,
                                    grant=row,
                                )
                            edges = await context.find_edges_between(
                                user.id, resource.id, edge_class=family
                            )
                            if len(edges) != 1:
                                raise AuthorizationUnavailableError()
                            await edges[0].delete()
                            removed += 1
                            if family in (COLLABORATES_ON, OWNS):
                                await record_grant_change(
                                    source=user,
                                    target=resource,
                                    grant_class=family,
                                    before=row,
                                    after=None,
                                )
                            if family is OWNS:
                                await actor.connect(resource, edge=OWNS, role="owner")
                                after = await context.database.find(
                                    "edge",
                                    {
                                        "source": actor.id,
                                        "target": resource.id,
                                        "entity": OWNS.__name__,
                                    },
                                    limit=2,
                                )
                                if len(after) != 1:
                                    raise AuthorizationUnavailableError()
                                await record_grant_change(
                                    source=actor,
                                    target=resource,
                                    grant_class=OWNS,
                                    before=None,
                                    after=after[0],
                                )
                                if isinstance(resource, App):
                                    resource.owner_user_id = actor.id
                                elif isinstance(resource, Track):
                                    resource.owner_id = actor.id
                    if "workspace_id" in type(resource).model_fields:
                        resource.workspace_id = destination_id
                    if isinstance(resource, App):
                        resource.lifecycle_state = "paused"
                    if isinstance(resource, (App, Track)) and resource.visibility in {
                        "workspace",
                        "organization",
                        "public",
                    }:
                        resource.visibility = "private"
                    await resource.save()
                for resource in roots:
                    for family in (CONTAINS, HAS_LIBRARY_MODEL):
                        for edge in await context.find_edges_between(
                            source.id, resource.id, edge_class=family
                        ):
                            await edge.delete()
                    if isinstance(resource, App):
                        await destination.connect(resource, edge=CONTAINS)
                        await _move_catalog(resource, source, destination, "apps")
                    elif isinstance(resource, Track):
                        # Contained tracks retain their App parent; standalone
                        # Tracks gain only the new workspace parent.
                        app_parents = await resource.nodes(
                            edge=[CONTAINS],
                            node=["WorkspaceApp"],
                            direction="in",
                            limit=2,
                        )
                        if not app_parents:
                            await destination.connect(resource, edge=CONTAINS)
                        await _move_catalog(resource, source, destination, "tracks")
                    else:
                        await destination.connect(resource, edge=HAS_LIBRARY_MODEL)
                for resource in resources.values():
                    kind = (
                        "app"
                        if isinstance(resource, App)
                        else (
                            "track"
                            if isinstance(resource, Track)
                            else "entry" if isinstance(resource, Entry) else None
                        )
                    )
                    if kind:
                        location = await locate_resource(
                            kind, resource.id, allowed_app_states=DATA_APP_STATES
                        )
                        if location is None or location.workspace_id != destination_id:
                            raise AuthorizationUnavailableError()
                result = {
                    "source_workspace_id": source_id,
                    "destination_workspace_id": destination_id,
                    "moved_apps": len(apps),
                    "moved_tracks": len(tracks),
                    "moved_models": len(libraries),
                    "removed_foreign_grants": removed,
                    "apps_paused": len(apps),
                    "outcome": "rehomed" if roots else "unchanged",
                }
                from app.services.app_operations.event_outbox import (
                    insert_revision_event,
                )

                outbox = await insert_revision_event(
                    transaction=get_default_context().database,
                    workspace_id=source_id,
                    namespace="workspace-rehome-v1",
                    subject_id=source_id,
                    revision=revisions[source_id].revision,
                    event={
                        "actor_kind": "human",
                        "actor_id": actor.id,
                        "action": "workspace.update",
                        "resource_type": "Workspace",
                        "resource_id": source_id,
                        "scope": f"user:{actor.user_id or actor.id}",
                        "before": None,
                        "after": {"operation": "workspace.rehome", **result},
                    },
                )
    from app.services.workspace_membership import _deliver

    await _deliver(outbox)
    return result


async def _move_catalog(resource, source, destination, kind: str) -> None:
    from app.services.workspace_lifecycle import _branch_for

    context = get_default_context()
    branch = await _branch_for(source.id, kind)
    if branch is None:
        raise AuthorizationUnavailableError()
    for edge in await context.find_edges_between(
        branch.id, resource.id, edge_class=CATALOGS
    ):
        await edge.delete()
    if kind == "apps":
        await catalog_app(resource)
    else:
        await catalog_track(resource)


async def _validate_rehome(resources: dict[str, Any], source_id: str) -> None:
    """Reject unknown execution sidecars and cross-boundary references upfront."""
    database = get_default_context().database
    identities = set(resources)
    if not identities:
        return
    references = await database.find(
        "edge",
        {
            "entity": REFERENCES.__name__,
            "$or": [
                {"source": {"$in": sorted(identities)}},
                {"target": {"$in": sorted(identities)}},
            ],
        },
        limit=10001,
    )
    if len(references) > 10000 or any(
        row.get("source") not in identities or row.get("target") not in identities
        for row in references
    ):
        raise ResourceConflictError(
            message="Cross-workspace references require reviewed migration"
        )
    for resource in resources.values():
        if isinstance(resource, OperationalModel):
            parents = await database.find(
                "edge",
                {"target": resource.id, "entity": HAS_OPERATIONAL_MODEL.__name__},
                limit=10001,
            )
            if len(parents) > 10000 or any(
                row.get("source") not in identities for row in parents
            ):
                raise ResourceConflictError(
                    message="Externally consumed Model requires reviewed migration"
                )
        if isinstance(resource, App):
            edges = await database.find("edge", {"source": resource.id}, limit=10001)
            permitted = {
                CONTAINS.__name__,
                CATALOGS.__name__,
                HAS_OPERATIONAL_MODEL.__name__,
            }
            if len(edges) > 10000 or any(
                row.get("entity") not in permitted for row in edges
            ):
                raise ResourceConflictError(
                    message="Attached execution/share subsystem requires reviewed migration"
                )
        if isinstance(resource, (App, Track)):
            kind = "app" if isinstance(resource, App) else "track"
            location = await locate_resource(kind, resource.id)
            if location is None or location.workspace_id != source_id:
                raise AuthorizationUnavailableError()
