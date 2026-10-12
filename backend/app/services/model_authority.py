"""Current graph authority for attached models, their drafts and templates.

Library publication has a separate ownership contract. It is deliberately not
inferred from an empty workspace cache or a platform catalog here.
"""

from dataclasses import dataclass

from jvspatial.core import Edge
from jvspatial.core.context import get_default_context
from jvspatial.core.decorators import on_visit
from jvspatial.core.entities import Root
from pydantic import PrivateAttr

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import (
    CATALOGS,
    DEFINES_TRACK_PROFILE,
    HAS_DRAFT_PROFILE,
    HAS_LIBRARY_MODEL,
    HAS_OPERATIONAL_MODEL,
    PUBLISHES_LIBRARY_MODEL,
)
from app.models.nodes import (
    APP_NODE_ID,
    OPERATIONAL_MODELS_REGISTRY_ID,
    App,
    IntegralApp,
    OperationalModel,
    OperationalModels,
    Track,
    Workspace,
)
from app.schemas.policy import Resource
from app.services.authority_facts import (
    relationship_flag,
    relationship_literal,
    required_node_text,
)
from app.services.resource_location import (
    DATA_APP_STATES,
    MANAGEMENT_APP_STATES,
    ResourceLocationWalker,
    locate_resource,
)


def optional_model_text(record: dict, field: str) -> str:
    """Read an explicitly persisted nullable cache, without coercion/defaults."""
    context = record.get("context")
    if not isinstance(context, dict):
        raise AuthorizationUnavailableError()
    if field not in context and field not in record:
        raise AuthorizationUnavailableError()
    if field in context and field in record and context[field] != record[field]:
        raise AuthorizationUnavailableError()
    value = context[field] if field in context else record[field]
    if value is None:
        return ""
    if not isinstance(value, str) or value != value.strip():
        raise AuthorizationUnavailableError()
    return value


@dataclass(frozen=True)
class ModelAuthority:
    """Complete named parent set; no reusable permission decision."""

    workspace_id: str
    owners: tuple[tuple[str, str], ...]
    editable: bool = True
    platform: bool = False
    library_model_id: str = ""
    unpublished: bool = False

    def matches_parent(self, scope: str, model_id: str) -> bool:
        """Require an exact current attachment, model self or workspace claim."""
        parts = scope.split(":", 1)
        if len(parts) != 2 or not parts[1] or parts[1] != parts[1].strip():
            return False
        kind, parent_id = parts
        if kind == "operational_model":
            return parent_id == model_id
        if kind == "workspace":
            return self.platform or parent_id == self.workspace_id
        return (kind, parent_id) in self.owners


class ModelAuthorityWalker(ResourceLocationWalker):
    """Traverse complete current model provenance into canonical resource roots."""

    _model_ids: set[str] = PrivateAttr(default_factory=set)
    _owners: set[tuple[str, str]] = PrivateAttr(default_factory=set)
    _workspace_hints: set[str] = PrivateAttr(default_factory=set)
    _app_hints: set[str] = PrivateAttr(default_factory=set)
    _historical: bool = PrivateAttr(default=False)
    _platform: bool = PrivateAttr(default=False)
    _library_model_id: str = PrivateAttr(default="")
    _unpublished: bool = PrivateAttr(default=False)

    async def check_library_catalog(self, context: object) -> bool:
        """Prove the unique Root → IntegralApp → library registry shell."""
        for model, node_id in (
            (Root, "n.Root.root"),
            (IntegralApp, APP_NODE_ID),
            (OperationalModels, OPERATIONAL_MODELS_REGISTRY_ID),
        ):
            if await self.read_node(model, node_id, context) is None:
                return False
        for target, source in (
            (APP_NODE_ID, "n.Root.root"),
            (OPERATIONAL_MODELS_REGISTRY_ID, APP_NODE_ID),
        ):
            parents = await self.parent_rows(target, Edge, 1, context)
            if len(parents) != 1 or parents[0]["source"] != source:
                return False
        return True

    @on_visit(OperationalModel)
    async def on_model(self, here: OperationalModel) -> None:
        """Reject mixed, missing, cyclic or cache-conflicting ownership paths."""
        context = await here.get_context()
        record = self._records[here.id]
        if here.id in self._model_ids or len(self._model_ids) >= 16:
            self.valid = False
            return
        self._model_ids.add(here.id)
        if (
            "library_package" not in record["context"]
            and "library_package" not in record
        ):
            raise AuthorizationUnavailableError()
        library = relationship_flag(record, "library_package")
        scope = required_node_text(record, "scope")
        status = relationship_literal(record, "status", {"published", "draft"})
        self._unpublished = self._unpublished or status == "draft"
        workspace_hint = optional_model_text(record, "workspace_id")
        app_hint = required_node_text(record, "app_id", allow_empty=True)
        if workspace_hint:
            self._workspace_hints.add(workspace_hint)
        if app_hint:
            self._app_hints.add(app_hint)
        drafts = await self.parent_rows(here.id, HAS_DRAFT_PROFILE, 1, context)
        templates = await self.parent_rows(here.id, DEFINES_TRACK_PROFILE, 1, context)
        attachments = await self.parent_rows(
            here.id, HAS_OPERATIONAL_MODEL, 256, context
        )
        catalogs = await self.parent_rows(here.id, CATALOGS, 1, context)
        library_owners = await self.parent_rows(here.id, HAS_LIBRARY_MODEL, 1, context)
        publications = await self.parent_rows(
            here.id, PUBLISHES_LIBRARY_MODEL, 1, context
        )
        draft_of = optional_model_text(record, "draft_of_id")
        if library:
            if draft_of or drafts or templates or attachments or app_hint:
                self.valid = False
                return
            if (
                len(catalogs) != 1
                or catalogs[0]["source"] != OPERATIONAL_MODELS_REGISTRY_ID
                or not await self.check_library_catalog(context)
            ):
                self.valid = False
                return
            if len(library_owners) + len(publications) != 1:
                self.valid = False
                return
            self._library_model_id = here.id
            if publications:
                if (
                    status != "published"
                    or publications[0]["source"] != APP_NODE_ID
                    or workspace_hint
                ):
                    self.valid = False
                    return
                self._platform = True
                return
            owner = await self.source_node(library_owners[0], (Workspace,), context)
            if owner is None or workspace_hint != owner.id:
                self.valid = False
                return
            self._owners.add(("workspace", owner.id))
            await self.visit(owner)
            return
        if library_owners or publications:
            self.valid = False
            return
        if catalogs:
            self.valid = False
            return
        if status == "draft" or draft_of or drafts:
            if len(drafts) != 1 or attachments or templates or not draft_of:
                self.valid = False
                return
            parent = await self.source_node(drafts[0], (OperationalModel,), context)
            if parent is None or parent.id != draft_of:
                self.valid = False
                return
            if status == "published":
                if optional_model_text(record, "published_id") != parent.id:
                    self.valid = False
                    return
                self._historical = True
            parent_record = self._records[parent.id]
            if (
                relationship_literal(parent_record, "status", {"published", "draft"})
                != "published"
                or required_node_text(parent_record, "scope") != scope
            ):
                self.valid = False
                return
            await self.visit(parent)
            return
        if draft_of or drafts:
            self.valid = False
            return
        if templates:
            if scope != "track" or len(templates) != 1 or attachments:
                self.valid = False
                return
            parent = await self.source_node(templates[0], (OperationalModel,), context)
            if parent is None or (
                not relationship_flag(self._records[parent.id], "library_package")
                and required_node_text(self._records[parent.id], "scope") != "app"
            ):
                self.valid = False
                return
            parent_record = self._records[parent.id]
            parent_app = required_node_text(parent_record, "app_id", allow_empty=True)
            if (
                relationship_flag(parent_record, "library_package")
                and (app_hint or parent_app)
            ) or (
                not relationship_flag(parent_record, "library_package")
                and (not app_hint or app_hint != parent_app)
            ):
                self.valid = False
                return
            await self.visit(parent)
            return
        if scope not in {"app", "track"}:
            self.valid = False
            return
        if not attachments or len(attachments) > 256:
            self.valid = False
            return
        owner_type = App if scope == "app" else Track
        ids = [row["source"] for row in attachments]
        if len(set(ids)) != len(ids) or (scope == "app" and len(ids) != 1):
            self.valid = False
            return
        for row in attachments:
            owner = await self.source_node(row, (owner_type,), context)
            if (
                owner is None
                or required_node_text(
                    self._records[owner.id], "attached_operational_model_id"
                )
                != here.id
            ):
                self.valid = False
                return
            if scope == "app" and app_hint != owner.id:
                self.valid = False
                return
            self._owners.add((scope, owner.id))
            await self.visit(owner)


async def resolve_model_authority(
    model_id: str, *, allowed_app_states: frozenset[str] = DATA_APP_STATES
) -> ModelAuthority | None:
    """Resolve fresh complete attached-model authority; withhold other families."""
    if not model_id:
        return None
    try:
        context = get_default_context()
        walker = ModelAuthorityWalker(allowed_app_states=allowed_app_states)
        model = await walker.read_node(OperationalModel, model_id, context)
        if model is None:
            return None
        await walker.spawn(model)
        if any(
            isinstance(item, dict) and "hook_error" in item
            for item in await walker.get_report()
        ):
            raise AuthorizationUnavailableError()
        if not walker.valid or (
            not walker._platform and (not walker.workspace_id or not walker._owners)
        ):
            return None
        if walker._workspace_hints - {walker.workspace_id}:
            return None
        # App caches cannot invent ownership. Every nonempty hint must name an
        # actual traversed App, whose own current workspace/root was checked.
        if any(
            app_id not in walker._records
            or walker._records[app_id].get("entity") != App._entity_name()
            for app_id in walker._app_hints
        ):
            return None
        return ModelAuthority(
            walker.workspace_id,
            tuple(sorted(walker._owners)),
            not walker._historical,
            walker._platform,
            walker._library_model_id,
            walker._unpublished,
        )
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


async def get_current_model(model_id: str) -> OperationalModel | None:
    """Capture a private Model body from primary storage without warmed getters.

    This capture grants no access. Callers must still admit its named authority
    and exact requested action before consuming or exposing the body.
    """
    try:
        return await ModelAuthorityWalker().read_node(
            OperationalModel, model_id, get_default_context()
        )
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


async def platform_seed_model(model_id: str) -> OperationalModel | None:
    """Load trusted seed identity, including retained inactive publications.

    This is a producer identity check, never a public read permission. Retired
    seeds keep their named publication provenance so restoration can preserve
    references. Empty workspace caches and matching metadata cannot adopt rows.
    """
    try:
        context = get_default_context()
        walker = ModelAuthorityWalker()
        model = await walker.read_node(OperationalModel, model_id, context)
        if model is None:
            return None
        record = walker._records[model_id]
        if (
            "library_package" not in record["context"]
            and "library_package" not in record
        ):
            raise AuthorizationUnavailableError()
        required_node_text(record, "scope")
        library = relationship_flag(record, "library_package")
        if (
            relationship_literal(record, "status", {"published", "draft"})
            != "published"
        ):
            return None
        metadata = record["context"].get("metadata")
        if not isinstance(metadata, dict) or metadata.get("seed_status") != (
            "active" if library else "inactive"
        ):
            return None
        if (
            optional_model_text(record, "workspace_id")
            or required_node_text(record, "app_id", allow_empty=True)
            or optional_model_text(record, "draft_of_id")
        ):
            return None
        for edge in (
            HAS_LIBRARY_MODEL,
            HAS_OPERATIONAL_MODEL,
            HAS_DRAFT_PROFILE,
            DEFINES_TRACK_PROFILE,
        ):
            if await walker.parent_rows(model_id, edge, 1, context):
                return None
        publications = await walker.parent_rows(
            model_id, PUBLISHES_LIBRARY_MODEL, 1, context
        )
        catalogs = await walker.parent_rows(model_id, CATALOGS, 1, context)
        if (
            len(publications) != 1
            or publications[0]["source"] != APP_NODE_ID
            or len(catalogs) != 1
            or catalogs[0]["source"] != OPERATIONAL_MODELS_REGISTRY_ID
            or not await walker.check_library_catalog(context)
        ):
            return None
        return model
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


async def admitted_model_draft(
    user_id: str, published_model_id: str, *, action: str = "operational_model.read"
) -> OperationalModel | None:
    """Select the unique current named draft without exposing reviewer metadata.

    Retained consumed versions do not count as pending drafts. Scalar lineage
    pointers cannot supply a sibling. A configuration caller must not fork a
    replacement after an existing named child fails admission.
    """
    from app.api.errors import InsufficientPermissionsError
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.services.authority_facts import primary_node_record

    try:
        with isolated_permissions_cache():
            context = get_default_context()
            rows = await context.database.find(
                "edge",
                {
                    "source": published_model_id,
                    "entity": HAS_DRAFT_PROFILE._entity_name(),
                },
                limit=257,
            )
            if len(rows) > 256:
                raise AuthorizationUnavailableError()
            pending = []
            for edge in rows:
                if (
                    edge.get("source") != published_model_id
                    or edge.get("entity") != HAS_DRAFT_PROFILE._entity_name()
                    or not isinstance(edge.get("target"), str)
                    or not edge["target"]
                ):
                    raise AuthorizationUnavailableError()
                record = await primary_node_record(
                    context.database, OperationalModel, edge["target"]
                )
                if record is None:
                    raise AuthorizationUnavailableError()
                if (
                    relationship_literal(record, "status", {"published", "draft"})
                    == "draft"
                ):
                    pending.append(record)
            if len(pending) > 1:
                raise AuthorizationUnavailableError()
            if not pending:
                return None
            record = pending[0]
            model = await context._deserialize_entity(OperationalModel, record)
            if model is None:
                raise AuthorizationUnavailableError()
            authority = await resolve_model_authority(
                model.id,
                allowed_app_states=(
                    DATA_APP_STATES
                    if action == "operational_model.read"
                    else MANAGEMENT_APP_STATES
                ),
            )
            if (
                authority is None
                or optional_model_text(record, "draft_of_id") != published_model_id
            ):
                raise AuthorizationUnavailableError()
            allowed = await model_action_authorized(
                user_id,
                action,
                Resource(
                    kind="operational_model",
                    id=model.id,
                    scope=f"operational_model:{model.id}",
                ),
            )
            if not allowed:
                if action != "operational_model.read":
                    raise InsufficientPermissionsError(message="Access denied")
                return None
            return model
    except InsufficientPermissionsError:
        raise
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


async def model_scope_matches(
    workspace_id: str, resource: Resource, *, principal_id: str
) -> bool:
    """Bind a model module target to its current ownership and execution scope."""
    authority = await resolve_model_authority(
        resource.id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if authority and authority.platform:
        from app.services.permissions import resolve_role

        if await resolve_role(principal_id, "workspace", workspace_id) is None:
            return False
        location = await locate_resource(
            "workspace", workspace_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if location is None:
            return False
        parts = resource.scope.split(":", 1)
        if parts[0] == "workspace" and (len(parts) != 2 or parts[1] != workspace_id):
            return False
        return authority.matches_parent(resource.scope, resource.id)
    return bool(
        authority
        and authority.workspace_id == workspace_id
        and authority.matches_parent(resource.scope, resource.id)
    )


async def model_action_authorized(
    user_id: str, action: str, resource: Resource
) -> bool:
    """Evaluate each Model boundary using fresh current permission facts."""
    from app.middleware.permissions_cache import isolated_permissions_cache

    with isolated_permissions_cache():
        return await _model_action_authorized(user_id, action, resource)


async def _model_action_authorized(
    user_id: str, action: str, resource: Resource
) -> bool:
    """Use a named read parent; configuration requires every affected parent."""
    from app.services.permissions import (
        can_admin_app,
        can_admin_track,
        can_view_app,
        can_view_track,
        get_user_node,
        resolve_role,
    )

    read = action == "operational_model.read"
    if not read and action not in {
        "operational_model.update",
        "operational_model.delete",
        "operational_model.merge_library",
        "operational_model.publish",
        "operational_model.rescan",
        "migration.publish",
        "migration.force_publish",
        "migration.run",
    }:
        return False
    authority = await resolve_model_authority(
        resource.id,
        allowed_app_states=DATA_APP_STATES if read else MANAGEMENT_APP_STATES,
    )
    if authority is None or not authority.matches_parent(resource.scope, resource.id):
        return False
    if not read and not authority.editable:
        return False
    if authority.platform:
        user = await get_user_node(user_id)
        if not read or user is None:
            return False
        if authority.unpublished:
            from app.services.permissions import _direct_role_on_resource

            return await _direct_role_on_resource(
                user, resource.id
            ) is not None or await _direct_role_on_resource(
                user, authority.library_model_id
            ) in {
                "owner",
                "admin",
            }
        return True
    if authority.library_model_id:
        from app.services.permissions import _direct_role_on_resource
        from app.services.workspace_governance import owner_governance_allowed
        from app.services.workspace_permissions import can_access_workspace

        user = await get_user_node(user_id)
        if user is None:
            return False
        workspace_role = await can_access_workspace(user_id, authority.workspace_id)
        if workspace_role not in {"owner", "admin", "member", "guest"}:
            return False
        direct = await _direct_role_on_resource(user, authority.library_model_id)
        if read:
            if authority.unpublished:
                return (
                    await _direct_role_on_resource(user, resource.id) is not None
                    or direct in {"owner", "admin"}
                    or (workspace_role == "owner" and owner_governance_allowed())
                )
            return direct is not None or workspace_role in {"owner", "admin", "member"}
        return direct in {"owner", "admin"} or (
            workspace_role == "owner" and owner_governance_allowed()
        )
    if read and authority.unpublished:
        from app.services.permissions import _direct_role_on_resource

        user = await get_user_node(user_id)
        if (
            user is None
            or await resolve_role(user_id, "workspace", authority.workspace_id) is None
        ):
            return False
        if await _direct_role_on_resource(user, resource.id) is not None:
            return True
        # Published-resource viewers do not automatically become draft reviewers.
        gates = {"app": can_admin_app, "track": can_admin_track}
    else:
        gates = (
            {"app": can_view_app, "track": can_view_track}
            if read
            else {"app": can_admin_app, "track": can_admin_track}
        )
    selected = authority.owners
    parts = tuple(resource.scope.split(":", 1))
    if read and parts in authority.owners:
        selected = (parts,)
    for kind, owner_id in selected:
        allowed = await gates[kind](user_id, owner_id)
        if read and allowed:
            return True
        if not read and not allowed:
            return False
    return not read
