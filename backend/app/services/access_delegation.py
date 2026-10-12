"""Current definition-bound action grants and flat workspace groups.

This is the declaration branch of the existing policy perimeter, not another
resource ACL: every action still requires live ordinary resource audience.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from jvspatial.api.exceptions import JVSpatialAPIException
from jvspatial.core import Walker
from jvspatial.core.context import get_default_context
from jvspatial.core.decorators import on_visit
from pydantic import Field, PrivateAttr

from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.access_delegation import (
    ACCESS_GROUP_MEMBER,
    ACTION_BINDING_SUBJECT,
    HAS_ACCESS_GROUP,
    HAS_ACTION_BINDING,
    AppActionBinding,
    WorkspaceAccessGroup,
)
from app.models.edges import HAS_APPLICATION_DEFINITION, IS_MEMBER_OF
from app.models.nodes import App, ApplicationDefinition, Workspace
from app.schemas.access_delegation import ActionAuthorization, ActionCondition
from app.schemas.policy import Decision
from app.services.authority_facts import primary_node_record, required_node_text
from app.services.grant_history import grant_baseline
from app.services.permissions import get_user_node, resolve_role
from app.services.resource_location import locate_resource
from app.services.workspace_governance import ordinary_authority_only
from app.services.workspace_permissions import can_access_workspace
from app.utils.time import utc_now_iso

MAX_BINDINGS = 256
MAX_GROUPS = 256


def _current_authority_read(function):
    """Map uncertain primary reads to unavailable without manufacturing an allow."""
    from functools import wraps

    @wraps(function)
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        try:
            return await function(*args, **kwargs)
        except JVSpatialAPIException:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError() from exc

    return invoke


async def _record(model: Any, identity: str) -> dict | None:
    return await primary_node_record(get_default_context().database, model, identity)


async def _edges(source: str, target: str, edge: Any) -> list[dict]:
    return await get_default_context().database.find(
        "edge",
        {"source": source, "target": target, "entity": edge._entity_name()},
        limit=2,
    )


@_current_authority_read
async def current_definition(
    app_id: str, *, execution: bool = True
) -> tuple[dict, dict]:
    """Require exact active pointer, unique attachment and canonical workspace."""
    app = await _record(App, app_id)
    from app.services.resource_location import ACTIVE_APP_STATES, DATA_APP_STATES

    if (
        app is None
        or await locate_resource(
            "app",
            app_id,
            allowed_app_states=ACTIVE_APP_STATES if execution else DATA_APP_STATES,
        )
        is None
    ):
        raise AuthorizationUnavailableError()
    definition_id = required_node_text(app, "active_definition_id")
    definition = await _record(ApplicationDefinition, definition_id)
    if (
        definition is None
        or len(await _edges(app_id, definition_id, HAS_APPLICATION_DEFINITION)) != 1
    ):
        raise AuthorizationUnavailableError()
    body = definition["context"]
    if (
        body.get("status") != "active"
        or body.get("app_id") != app_id
        or body.get("workspace_id") != app["context"].get("workspace_id")
        or body.get("revision") != app["context"].get("active_definition_revision")
    ):
        raise AuthorizationUnavailableError()
    from app.services.application_definitions import definition_fingerprint

    if not isinstance(body.get("canonical_manifest"), dict) or definition_fingerprint(
        body["canonical_manifest"]
    ) != body.get("manifest_fingerprint"):
        raise AuthorizationUnavailableError()
    active = await get_default_context().database.find(
        "node",
        {
            "entity": ApplicationDefinition._entity_name(),
            "context.app_id": app_id,
            "context.status": "active",
        },
        limit=2,
    )
    if len(active) != 1 or active[0].get("id") != definition_id:
        raise AuthorizationUnavailableError()
    return app, definition


@_current_authority_read
async def membership_baseline(principal_id: str, workspace_id: str) -> tuple[Any, str]:
    """Bind group/action enrollment to the current membership lifetime."""
    user = await get_user_node(principal_id)
    if user is None or await can_access_workspace(user.id, workspace_id) == "none":
        return None, ""
    rows = await _edges(user.id, workspace_id, IS_MEMBER_OF)
    if len(rows) != 1:
        raise AuthorizationUnavailableError()
    return user, await grant_baseline(
        source_id=user.id,
        target_id=workspace_id,
        grant_class=IS_MEMBER_OF,
        grant=rows[0],
    )


async def _group_member(
    user: Any, workspace_id: str, group_id: str, baseline: str
) -> bool:
    group = await _record(WorkspaceAccessGroup, group_id)
    if group is None:
        return False
    if required_node_text(group, "workspace_id") != workspace_id:
        return False
    roots = await get_default_context().database.find(
        "edge", {"target": group_id, "entity": HAS_ACCESS_GROUP._entity_name()}, limit=2
    )
    if len(roots) != 1 or roots[0].get("source") != workspace_id:
        raise AuthorizationUnavailableError()
    rows = await _edges(user.id, group_id, ACCESS_GROUP_MEMBER)
    if len(rows) > 1:
        raise AuthorizationUnavailableError()
    if not rows:
        return False
    body = rows[0].get("context")
    if not isinstance(body, dict):
        raise AuthorizationUnavailableError()
    return (
        required_node_text(rows[0], "workspace_id") == workspace_id
        and required_node_text(rows[0], "membership_fingerprint") == baseline
    )


async def _targets(
    declaration: ActionAuthorization,
    payload: dict,
    app_id: str,
    workspace_id: str,
    principal_id: str,
) -> list[dict]:
    from app.models.nodes import Entry, Track

    targets = []
    for target in declaration.targets:
        identity = payload.get(target.input)
        if not isinstance(identity, str) or not identity:
            return []
        location = await locate_resource(target.kind, identity)
        if (
            location is None
            or location.app_id != app_id
            or location.workspace_id != workspace_id
        ):
            return []
        if await resolve_role(principal_id, target.kind, identity) is None:
            return []
        row = await _record(Entry if target.kind == "entry" else Track, identity)
        if row is None:
            return []
        if target.kind == "entry":
            from app.models.edges import AUTHORED_BY

            authors = await get_default_context().database.find(
                "edge",
                {"source": identity, "entity": AUTHORED_BY._entity_name()},
                limit=2,
            )
            if len(authors) != 1:
                raise AuthorizationUnavailableError()
            from app.models.nodes import User

            author = await _record(User, authors[0].get("target", ""))
            if author is None:
                raise AuthorizationUnavailableError()
            row = {**row, "context": {**row["context"], "author_id": author["id"]}}
        targets.append(row)
    return targets


def conditions_match(
    conditions: list[ActionCondition], targets: list[dict], principal_id: str
) -> bool:
    """Evaluate each complete grant independently against server-read targets."""
    if conditions and not targets:
        return False
    for condition in conditions:
        expected = principal_id if condition.principal else condition.value
        for row in targets:
            body = row["context"]
            missing = object()
            if condition.attribute == "entry.custom_fields":
                value = (body.get("custom_fields") or {}).get(condition.field, missing)
            elif condition.attribute == "entry.author_id":
                value = body.get("author_id", missing)
            else:
                value = body.get("status", missing)
            if value is missing:
                return False
            if condition.operator == "eq" and value != expected:
                return False
            if condition.operator == "ne" and value == expected:
                return False
            if condition.operator == "in" and (
                not isinstance(expected, list) or value not in expected
            ):
                return False
    return True


class AppActionGrantWalker(Walker):
    """Traverse App grants and flat groups without procedural graph recursion."""

    workspace_id: str
    app_id: str
    definition_id: str
    fingerprint: str
    user_id: str
    baseline: str
    roles: list[str]
    targets: list[dict]
    matched_ids: list[str] = Field(default_factory=list)
    _issuance_only: bool = PrivateAttr(default=False)
    _records: dict[str, dict] = PrivateAttr(default_factory=dict)
    _group_grants: dict[str, list[str]] = PrivateAttr(default_factory=dict)

    @on_visit(App)
    async def on_app(self, app: App) -> None:
        """Check all raw candidates before hydration can hide malformed grants."""
        rows = await get_default_context().database.find(
            "edge",
            {"source": self.app_id, "entity": HAS_ACTION_BINDING._entity_name()},
            limit=MAX_BINDINGS + 1,
        )
        if len(rows) > MAX_BINDINGS:
            raise AuthorizationUnavailableError()
        seen = set()
        for edge in rows:
            identity = edge.get("target")
            if not isinstance(identity, str) or not identity or identity in seen:
                raise AuthorizationUnavailableError()
            seen.add(identity)
            row = await _record(AppActionBinding, identity)
            if row is None:
                raise AuthorizationUnavailableError()
            body = row["context"]
            if type(body.get("active")) is not bool:
                raise AuthorizationUnavailableError()
            if not body["active"] or body.get("definition_id") != self.definition_id:
                continue
            if (
                body.get("app_id") != self.app_id
                or body.get("workspace_id") != self.workspace_id
                or body.get("definition_fingerprint") != self.fingerprint
            ):
                raise AuthorizationUnavailableError()
            if body.get("role") not in self.roles:
                continue
            expiry = body.get("expires_at")
            if expiry:
                instant = datetime.fromisoformat(expiry)
                if instant.tzinfo is None:
                    raise AuthorizationUnavailableError()
                if instant <= datetime.now(timezone.utc):
                    continue
            subjects = await get_default_context().database.find(
                "edge",
                {"source": identity, "entity": ACTION_BINDING_SUBJECT._entity_name()},
                limit=2,
            )
            if (
                len(subjects) != 1
                or subjects[0].get("target") != body.get("subject_id")
                or (subjects[0].get("context") or {}).get("subject_kind")
                != body.get("subject_kind")
            ):
                raise AuthorizationUnavailableError()
            if not isinstance(body.get("conditions"), list):
                raise AuthorizationUnavailableError()
            validated_conditions = [
                ActionCondition.model_validate(c) for c in body["conditions"]
            ]
            if not self._issuance_only and not conditions_match(
                validated_conditions, self.targets, self.user_id
            ):
                continue
            node = await get_default_context()._deserialize_entity(
                AppActionBinding, row
            )
            if node is None:
                raise AuthorizationUnavailableError()
            self._records[identity] = row
            await self.visit(node)

    @on_visit(AppActionBinding)
    async def on_binding(self, binding: AppActionBinding) -> None:
        """Each complete binding keeps its own conditions and exact subject."""
        body = self._records[binding.id]["context"]
        if body["subject_kind"] == "human":
            if (
                body["subject_id"] == self.user_id
                and body.get("membership_fingerprint") == self.baseline
            ):
                self.matched_ids.append(binding.id)
        elif body["subject_kind"] == "group":
            row = await _record(WorkspaceAccessGroup, body["subject_id"])
            if row is None:
                raise AuthorizationUnavailableError()
            self._group_grants.setdefault(row["id"], []).append(binding.id)
            node = await get_default_context()._deserialize_entity(
                WorkspaceAccessGroup, row
            )
            if node is None:
                raise AuthorizationUnavailableError()
            await self.visit(node)
        else:
            raise AuthorizationUnavailableError()

    @on_visit(WorkspaceAccessGroup)
    async def on_group(self, group: WorkspaceAccessGroup) -> None:
        """Evaluate current user membership on the independently rooted group."""
        user = await get_user_node(self.user_id)
        if user is not None and await _group_member(
            user, self.workspace_id, group.id, self.baseline
        ):
            self.matched_ids.extend(self._group_grants.get(group.id, []))


@_current_authority_read
async def action_decision(
    *, principal_id: str, app_id: str, operation_key: str, payload: dict | None = None
) -> Decision:
    """Current explicit action grant intersected with ordinary App audience."""
    with isolated_permissions_cache(), ordinary_authority_only():
        app, definition = await current_definition(app_id)
        workspace_id = required_node_text(app, "workspace_id")
        user, baseline = await membership_baseline(principal_id, workspace_id)
        if user is None or await resolve_role(user.id, "app", app_id) is None:
            return Decision(allowed=False, reason="app_action_audience_denied")
        manifest = definition["context"].get("canonical_manifest") or {}
        operations = (manifest.get("app") or {}).get("operations") or []
        specs = [op for op in operations if op.get("key") == operation_key]
        if len(specs) != 1 or not specs[0].get("authorization"):
            return Decision(allowed=False, reason="app_action_unknown")
        declaration = ActionAuthorization.model_validate(specs[0]["authorization"])
        targets = await _targets(
            declaration, payload or {}, app_id, workspace_id, user.id
        )
        if len(targets) != len(declaration.targets) or not conditions_match(
            declaration.conditions, targets, user.id
        ):
            return Decision(allowed=False, reason="app_action_condition_denied")
        walker = AppActionGrantWalker(
            workspace_id=workspace_id,
            app_id=app_id,
            definition_id=definition["id"],
            fingerprint=definition["context"]["manifest_fingerprint"],
            user_id=user.id,
            baseline=baseline,
            roles=declaration.roles,
            targets=targets,
        )
        current_app = await get_default_context()._deserialize_entity(App, app)
        if current_app is None:
            raise AuthorizationUnavailableError()
        await walker.spawn(current_app)
        if walker.matched_ids:
            return Decision(
                allowed=True,
                reason="app_action_binding",
                policy_chain=walker.matched_ids,
            )
        return Decision(allowed=False, reason="app_action_no_grant")


async def _require_owner(actor_id: str, workspace_id: str) -> Any:
    from app.services.workspace_permissions import _current_workspace_role

    actor = await get_user_node(actor_id)
    workspace = await Workspace.find_one({"id": workspace_id})
    if (
        actor is None
        or workspace is None
        or await _current_workspace_role(actor, workspace) != "owner"
    ):
        from app.api.errors import InsufficientPermissionsError

        raise InsufficientPermissionsError(message="Workspace owner authority required")
    return actor


async def create_group(
    *, actor_id: str, workspace_id: str, name: str
) -> WorkspaceAccessGroup:
    """Create a workspace-rooted flat group under current owner authority."""
    from app.services.sharing_write_scope import sharing_write_scope

    async with sharing_write_scope(workspace_id, principal_ids=(actor_id,)):
        actor = await _require_owner(actor_id, workspace_id)
        workspace = await Workspace.find_one({"id": workspace_id})
        if workspace is None:
            raise AuthorizationUnavailableError()
        if (
            await workspace.count_nodes(
                edge=[HAS_ACCESS_GROUP], node=["WorkspaceAccessGroup"], direction="out"
            )
            >= MAX_GROUPS
        ):
            from app.api.errors import BadRequestError

            raise BadRequestError(message="Workspace group limit reached")
        group = await WorkspaceAccessGroup.create(
            workspace_id=workspace_id,
            name=name,
            created_by=actor.id,
            created_at=utc_now_iso(),
        )
        await workspace.connect(group, edge=HAS_ACCESS_GROUP)
        await _audit(actor.id, workspace_id, group, "access_group.create")
        return group


async def set_group_member(
    *, actor_id: str, group_id: str, user_id: str, member: bool
) -> None:
    """Change group membership under the shared workspace revision fence."""
    from app.services.sharing_write_scope import sharing_write_scope

    group = await _record(WorkspaceAccessGroup, group_id)
    if group is None:
        from app.api.errors import ResourceNotFoundError

        raise ResourceNotFoundError(message="Group not found")
    workspace_id = required_node_text(group, "workspace_id")
    async with sharing_write_scope(
        workspace_id,
        principal_ids=(actor_id, user_id),
        active_principal_ids=(actor_id, user_id) if member else (actor_id,),
    ):
        actor = await _require_owner(actor_id, workspace_id)
        if member:
            user, baseline = await membership_baseline(user_id, workspace_id)
        else:
            from app.services.identity_admission import resolve_current_profile

            user = await resolve_current_profile(user_id, require_active=False)
            baseline = ""
        if user is None:
            from app.api.errors import InsufficientPermissionsError

            raise InsufficientPermissionsError(
                message="Current workspace membership required"
            )
        if len(await _edges(workspace_id, group_id, HAS_ACCESS_GROUP)) != 1:
            raise AuthorizationUnavailableError()
        node = await WorkspaceAccessGroup.find_one({"id": group_id})
        if not isinstance(node, WorkspaceAccessGroup):
            raise AuthorizationUnavailableError()
        edges = await get_default_context().find_edges_between(
            user.id, group_id, edge_class=ACCESS_GROUP_MEMBER
        )
        for edge in edges:
            await edge.delete()
        if member:
            await user.connect(
                node,
                edge=ACCESS_GROUP_MEMBER,
                workspace_id=workspace_id,
                membership_fingerprint=baseline,
                granted_by=actor.id,
                granted_at=utc_now_iso(),
            )

        node.revision += 1
        await node.save()
        await _audit(
            actor.id,
            workspace_id,
            node,
            "access_group.member.add" if member else "access_group.member.remove",
            subject_id=user.id,
        )


async def create_binding(
    *,
    actor_id: str,
    app_id: str,
    subject_kind: str,
    subject_id: str,
    role: str,
    conditions: list[dict],
    expires_at: str | None = None,
) -> AppActionBinding:
    """Create one independently conditioned definition-bound function grant."""
    from app.api.errors import BadRequestError, InsufficientPermissionsError
    from app.services.sharing_write_scope import sharing_write_scope

    app, _ = await current_definition(app_id)
    workspace_id = required_node_text(app, "workspace_id")
    async with sharing_write_scope(
        workspace_id,
        principal_ids=(
            (actor_id, subject_id) if subject_kind == "human" else (actor_id,)
        ),
    ):
        actor = await _require_owner(actor_id, workspace_id)
        app, definition = await current_definition(app_id)
        if required_node_text(app, "workspace_id") != workspace_id:
            raise AuthorizationUnavailableError()
        operations = (
            (definition["context"].get("canonical_manifest") or {}).get("app") or {}
        ).get("operations", [])
        roles = {
            r
            for op in operations
            if op.get("authorization")
            for r in ActionAuthorization.model_validate(op["authorization"]).roles
        }
        if role not in roles:
            raise BadRequestError(message="Role is absent from active App definition")
        baseline = ""
        if subject_kind == "human":
            user, baseline = await membership_baseline(subject_id, workspace_id)
            if user is None:
                raise InsufficientPermissionsError(
                    message="Current workspace membership required"
                )
            subject_id = user.id
            subject_node = user
        elif subject_kind == "group":
            group = await _record(WorkspaceAccessGroup, subject_id)
            if (
                group is None
                or required_node_text(group, "workspace_id") != workspace_id
                or len(await _edges(workspace_id, subject_id, HAS_ACCESS_GROUP)) != 1
            ):
                raise InsufficientPermissionsError(message="Group outside workspace")
            subject_node = await WorkspaceAccessGroup.find_one({"id": subject_id})
            if subject_node is None:
                raise AuthorizationUnavailableError()
        else:
            raise BadRequestError(message="Unsupported action subject")
        validated = [ActionCondition.model_validate(c).model_dump() for c in conditions]
        if expires_at and datetime.fromisoformat(expires_at).tzinfo is None:
            raise BadRequestError(message="Expiry requires a timezone")
        node = await App.find_one({"id": app_id})
        if node is None:
            raise AuthorizationUnavailableError()
        if (
            await node.count_nodes(
                edge=[HAS_ACTION_BINDING], node=["AppActionBinding"], direction="out"
            )
            >= MAX_BINDINGS
        ):
            raise BadRequestError(message="App binding limit reached")
        binding = await AppActionBinding.create(
            app_id=app_id,
            workspace_id=workspace_id,
            definition_id=definition["id"],
            definition_fingerprint=definition["context"]["manifest_fingerprint"],
            subject_kind=subject_kind,
            subject_id=subject_id,
            role=role,
            conditions=validated,
            expires_at=expires_at,
            membership_fingerprint=baseline,
            created_by=actor.id,
            created_at=utc_now_iso(),
        )
        await node.connect(binding, edge=HAS_ACTION_BINDING)
        await binding.connect(
            subject_node, edge=ACTION_BINDING_SUBJECT, subject_kind=subject_kind
        )
        await _audit(actor.id, workspace_id, binding, "app_action.grant")
        return binding


async def revoke_binding(*, actor_id: str, binding_id: str) -> None:
    """Retain the revoked source for review while ending its authority."""
    from app.services.sharing_write_scope import sharing_write_scope

    row = await _record(AppActionBinding, binding_id)
    if row is None:
        from app.api.errors import ResourceNotFoundError

        raise ResourceNotFoundError(message="Action binding not found")
    workspace_id = required_node_text(row, "workspace_id")
    async with sharing_write_scope(workspace_id, principal_ids=(actor_id,)):
        actor = await _require_owner(actor_id, workspace_id)
        binding = await AppActionBinding.find_one({"id": binding_id})
        if binding is None:
            raise AuthorizationUnavailableError()
        if binding.active:
            binding.active = False
            binding.revision += 1
            await binding.save()
            await _audit(actor.id, workspace_id, binding, "app_action.revoke")


def _owner_read(function):
    """Order bounded owner inventory release with current identity and workspace authority."""
    import inspect
    from contextlib import nullcontext
    from functools import wraps

    signature = inspect.signature(function)

    @wraps(function)
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        from app.services.app_operations.transaction_scope import (
            graph_transaction_available,
            postgres_graph_transaction,
        )
        from app.services.authorization_scope import authorization_scope
        from app.services.identity_admission import identity_admission

        values = signature.bind(*args, **kwargs)
        actor_id = values.arguments["actor_id"]
        app_id = values.arguments.get("app_id")
        workspace_id = values.arguments.get("workspace_id")
        if app_id:
            app, _ = await current_definition(app_id, execution=False)
            workspace_id = required_node_text(app, "workspace_id")
        elif values.arguments.get("group_id"):
            workspace_id = await group_workspace(values.arguments["group_id"])
        if not workspace_id:
            raise AuthorizationUnavailableError()
        database = get_default_context().database
        transactional = graph_transaction_available(database)
        transaction = (
            postgres_graph_transaction(database) if transactional else nullcontext()
        )
        with isolated_permissions_cache():
            async with transaction, identity_admission((actor_id,)):
                fence = (
                    authorization_scope(workspace_id)
                    if transactional
                    else nullcontext()
                )
                async with fence:
                    await _require_owner(actor_id, workspace_id)
                    result = await function(*args, **kwargs)
                    if app_id:
                        app, _ = await current_definition(app_id, execution=False)
                        if required_node_text(app, "workspace_id") != workspace_id:
                            raise AuthorizationUnavailableError()
                    elif (
                        values.arguments.get("group_id")
                        and await group_workspace(values.arguments["group_id"])
                        != workspace_id
                    ):
                        raise AuthorizationUnavailableError()
                    await _require_owner(actor_id, workspace_id)
                    return result

    return invoke


@_owner_read
async def list_groups(
    *, actor_id: str, workspace_id: str, cursor: str | None = None, limit: int = 50
) -> dict:
    """Paginate the complete owner-only workspace group directory."""
    from app.api.errors import BadRequestError

    if limit < 1 or limit > 100:
        raise BadRequestError(message="Invalid pagination")
    await _require_owner(actor_id, workspace_id)
    workspace = await Workspace.find_one({"id": workspace_id})
    if workspace is None:
        raise AuthorizationUnavailableError()
    groups, page = await workspace.nodes_page(
        edge=[HAS_ACCESS_GROUP],
        node=["WorkspaceAccessGroup"],
        direction="out",
        cursor=cursor,
        limit=limit,
    )
    return {
        "groups": [await group.export(flat=True) for group in groups],
        "next_cursor": page,
    }


@_owner_read
async def list_bindings(
    *, actor_id: str, app_id: str, cursor: str | None = None, limit: int = 50
) -> dict:
    """Paginate definition-specific bindings including stale review records."""
    from app.api.errors import BadRequestError

    if limit < 1 or limit > 100:
        raise BadRequestError(message="Invalid pagination")
    app, definition = await current_definition(app_id, execution=False)
    await _require_owner(actor_id, required_node_text(app, "workspace_id"))
    node = await App.find_one({"id": app_id})
    if node is None:
        raise AuthorizationUnavailableError()
    bindings, page = await node.nodes_page(
        edge=[HAS_ACTION_BINDING],
        node=["AppActionBinding"],
        direction="out",
        cursor=cursor,
        limit=limit,
    )
    operations = (definition["context"]["canonical_manifest"].get("app") or {}).get(
        "operations", []
    )
    return {
        "bindings": [await binding.export(flat=True) for binding in bindings],
        "next_cursor": page,
        "definition_id": definition["id"],
        "role_actions": {
            role: [
                operation["key"]
                for operation in operations
                if role in (operation.get("authorization") or {}).get("roles", [])
            ]
            for role in sorted(
                {
                    role
                    for operation in operations
                    for role in (operation.get("authorization") or {}).get("roles", [])
                }
            )
        },
    }


async def _audit(
    actor_id: str, workspace_id: str, node: Any, action: str, **details: Any
) -> None:
    from app.services.change_event import emit_change_event
    from app.services.sharing_write_scope import emit_sharing_change

    await emit_sharing_change(
        clock={"id": node.id, "context": {"revision": node.revision}},
        emitter=emit_change_event,
        actor_kind="human",
        actor_id=actor_id,
        action=(
            "app.update" if isinstance(node, AppActionBinding) else "workspace.update"
        ),
        resource_type="App" if isinstance(node, AppActionBinding) else "Workspace",
        resource_id=node.app_id if isinstance(node, AppActionBinding) else workspace_id,
        before=None,
        after={
            "access_change": action,
            "grant_id": node.id,
            "revision": node.revision,
            **details,
        },
        scope=f"ws:{workspace_id}",
    )


@_owner_read
async def list_group_members(
    *, actor_id: str, group_id: str, cursor: str | None = None, limit: int = 50
) -> dict:
    """Owner directory exposes minimal profiles and bound membership state."""
    from app.api.errors import BadRequestError, ResourceNotFoundError
    from app.api.utils import public_user_view

    if limit < 1 or limit > 100:
        raise BadRequestError(message="Invalid pagination")
    row = await _record(WorkspaceAccessGroup, group_id)
    if row is None:
        raise ResourceNotFoundError(message="Group not found")
    workspace_id = required_node_text(row, "workspace_id")
    await _require_owner(actor_id, workspace_id)
    if len(await _edges(workspace_id, group_id, HAS_ACCESS_GROUP)) != 1:
        raise AuthorizationUnavailableError()
    group = await WorkspaceAccessGroup.find_one({"id": group_id})
    if group is None:
        raise AuthorizationUnavailableError()
    users, page = await group.nodes_page(
        edge=[ACCESS_GROUP_MEMBER],
        node=["User"],
        direction="in",
        cursor=cursor,
        limit=limit,
    )
    members = []
    for user in users:
        current, baseline = await membership_baseline(user.id, workspace_id)
        live = current is not None and await _group_member(
            current, workspace_id, group_id, baseline
        )
        members.append({"user": await public_user_view(user), "active": live})
    return {"members": members, "next_cursor": page}


async def delete_group(*, actor_id: str, group_id: str) -> None:
    """Delete a rooted group; its action bindings become non-authoritative."""
    from app.api.errors import ResourceNotFoundError
    from app.services.sharing_write_scope import sharing_write_scope

    row = await _record(WorkspaceAccessGroup, group_id)
    if row is None:
        raise ResourceNotFoundError(message="Group not found")
    workspace_id = required_node_text(row, "workspace_id")
    async with sharing_write_scope(workspace_id, principal_ids=(actor_id,)):
        actor = await _require_owner(actor_id, workspace_id)
        if len(await _edges(workspace_id, group_id, HAS_ACCESS_GROUP)) != 1:
            raise AuthorizationUnavailableError()
        group = await WorkspaceAccessGroup.find_one({"id": group_id})
        if group is None:
            raise AuthorizationUnavailableError()
        bindings = await get_default_context().database.find(
            "node",
            {
                "entity": AppActionBinding._entity_name(),
                "context.workspace_id": workspace_id,
                "context.subject_kind": "group",
                "context.subject_id": group_id,
            },
            limit=1025,
        )
        if len(bindings) > 1024:
            raise AuthorizationUnavailableError()
        for row in bindings:
            binding = await get_default_context()._deserialize_entity(
                AppActionBinding, row
            )
            if binding is None:
                raise AuthorizationUnavailableError()
            if binding.active:
                binding.active = False
                binding.revision += 1
                await binding.save()
                await _audit(actor.id, workspace_id, binding, "app_action.revoke")
        group.revision += 1
        await _audit(actor.id, workspace_id, group, "access_group.delete")
        await group.delete(cascade=False)


@_current_authority_read
async def _subject_delegation_facts(user_id: str, workspace_id: str) -> dict:
    """Hash current rooted declaration dependencies for reviewed restoration.

    This read does not admit the suspended/disabled subject. Root owner-review
    authority is checked by the lifecycle caller; the digest conveys no IDs.
    """
    from app.models.nodes import User

    database = get_default_context().database
    profile = await _record(User, user_id)
    if profile is None:
        profiles = await database.find(
            "node", {"entity": User._entity_name(), "context.user_id": user_id}, limit=2
        )
        if len(profiles) != 1:
            raise AuthorizationUnavailableError()
        profile = profiles[0]
        if required_node_text(profile, "user_id") != user_id:
            raise AuthorizationUnavailableError()
    identity = profile["id"]
    groups = await database.find(
        "node",
        {
            "entity": WorkspaceAccessGroup._entity_name(),
            "context.workspace_id": workspace_id,
        },
        limit=MAX_GROUPS + 1,
    )
    if len(groups) > MAX_GROUPS:
        raise AuthorizationUnavailableError()
    group_ids = []
    for group in groups:
        if len(await _edges(workspace_id, group["id"], HAS_ACCESS_GROUP)) != 1:
            raise AuthorizationUnavailableError()
        group_ids.append(group["id"])
    memberships = (
        await database.find(
            "edge",
            {
                "entity": ACCESS_GROUP_MEMBER._entity_name(),
                "source": identity,
                "target": {"$in": group_ids},
            },
            limit=MAX_GROUPS + 1,
        )
        if group_ids
        else []
    )
    if len(memberships) > MAX_GROUPS:
        raise AuthorizationUnavailableError()
    relevant_groups = {edge["target"] for edge in memberships}
    bindings = await database.find(
        "node",
        {
            "entity": AppActionBinding._entity_name(),
            "context.workspace_id": workspace_id,
            "$or": [
                {"context.subject_kind": "human", "context.subject_id": identity},
                {
                    "context.subject_kind": "group",
                    "context.subject_id": {"$in": list(relevant_groups)},
                },
            ],
        },
        limit=1025,
    )
    if len(bindings) > 1024:
        raise AuthorizationUnavailableError()
    definitions = []
    for app_id in sorted(
        {required_node_text(binding, "app_id") for binding in bindings}
    ):
        from app.services.resource_location import DATA_APP_STATES

        location = await locate_resource(
            "app", app_id, allowed_app_states=DATA_APP_STATES
        )
        if location is None or location.workspace_id != workspace_id:
            raise AuthorizationUnavailableError()
        for binding in bindings:
            if (
                required_node_text(binding, "app_id") == app_id
                and len(await _edges(app_id, binding["id"], HAS_ACTION_BINDING)) != 1
            ):
                raise AuthorizationUnavailableError()
        app, definition = await current_definition(app_id, execution=False)
        definitions.append(
            {
                "app_id": app_id,
                "definition_id": definition["id"],
                "fingerprint": definition["context"]["manifest_fingerprint"],
            }
        )
    facts = {
        "profile_id": identity,
        "workspace_id": workspace_id,
        "memberships": sorted(memberships, key=lambda row: row["id"]),
        "groups": sorted(
            [group for group in groups if group["id"] in relevant_groups],
            key=lambda row: row["id"],
        ),
        "bindings": sorted(bindings, key=lambda row: row["id"]),
        "definitions": definitions,
    }
    return facts


async def subject_delegation_fingerprint(user_id: str, workspace_id: str) -> str:
    """Hash current workspace-local rooted grant and definition facts."""
    import hashlib
    import json

    facts = await _subject_delegation_facts(user_id, workspace_id)
    return hashlib.sha256(
        json.dumps(
            facts, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


async def subject_delegation_review(user_id: str, workspace_id: str) -> dict:
    """Project retained sources for the already owner-authorized review caller."""
    facts = await _subject_delegation_facts(user_id, workspace_id)
    return {
        "groups": [
            {"group_id": row["id"], "name": required_node_text(row, "name")}
            for row in facts["groups"]
        ],
        "action_bindings": [
            {
                "binding_id": row["id"],
                "app_id": required_node_text(row, "app_id"),
                "role": required_node_text(row, "role"),
                "active": row["context"].get("active") is True,
                "expires_at": row["context"].get("expires_at"),
            }
            for row in facts["bindings"]
        ],
    }


@_current_authority_read
async def workspace_delegation_roots(workspace_id: str) -> list[Any]:
    """Collect all rooted declaration records before authorized workspace delete.

    This provides structural cleanup, never an authorization grant. The caller
    holds the existing owner-governed workspace lifecycle unit and fence.
    """
    from app.services.resource_location import KNOWN_APP_STATES

    database = get_default_context().database
    groups = await database.find(
        "node",
        {
            "entity": WorkspaceAccessGroup._entity_name(),
            "context.workspace_id": workspace_id,
        },
        limit=MAX_GROUPS + 1,
    )
    bindings = await database.find(
        "node",
        {
            "entity": AppActionBinding._entity_name(),
            "context.workspace_id": workspace_id,
        },
        limit=16385,
    )
    if len(groups) > MAX_GROUPS or len(bindings) > 16384:
        raise AuthorizationUnavailableError()
    roots = []
    for row in groups:
        edges = await database.find(
            "edge",
            {"target": row["id"], "entity": HAS_ACCESS_GROUP._entity_name()},
            limit=2,
        )
        if len(edges) != 1 or edges[0].get("source") != workspace_id:
            raise AuthorizationUnavailableError()
        node = await get_default_context()._deserialize_entity(
            WorkspaceAccessGroup, row
        )
        if node is None:
            raise AuthorizationUnavailableError()
        roots.append(node)
    for row in bindings:
        app_id = required_node_text(row, "app_id")
        location = await locate_resource(
            "app", app_id, allowed_app_states=KNOWN_APP_STATES
        )
        edges = await database.find(
            "edge",
            {"target": row["id"], "entity": HAS_ACTION_BINDING._entity_name()},
            limit=2,
        )
        if (
            location is None
            or location.workspace_id != workspace_id
            or len(edges) != 1
            or edges[0].get("source") != app_id
        ):
            raise AuthorizationUnavailableError()
        node = await get_default_context()._deserialize_entity(AppActionBinding, row)
        if node is None:
            raise AuthorizationUnavailableError()
        roots.append(node)
    return roots


async def delete_workspace_delegation_roots(workspace_id: str) -> None:
    """Delete reviewed workspace-owned declaration roots inside lifecycle unit."""
    roots = await workspace_delegation_roots(workspace_id)
    for node in roots:
        await node.delete(cascade=False)


@_current_authority_read
async def group_workspace(group_id: str) -> str:
    """Resolve the unique rooted group workspace before scope or mutation."""
    from app.api.errors import ResourceNotFoundError

    row = await _record(WorkspaceAccessGroup, group_id)
    if row is None:
        raise ResourceNotFoundError(message="Group not found")
    workspace_id = required_node_text(row, "workspace_id")
    roots = await get_default_context().database.find(
        "edge", {"target": group_id, "entity": HAS_ACCESS_GROUP._entity_name()}, limit=2
    )
    if (
        len(roots) != 1
        or roots[0].get("source") != workspace_id
        or await locate_resource("workspace", workspace_id) is None
    ):
        raise AuthorizationUnavailableError()
    return workspace_id


@_current_authority_read
async def binding_workspace(binding_id: str) -> str:
    """Resolve App-rooted grant location without trusting its workspace cache."""
    from app.api.errors import ResourceNotFoundError
    from app.services.resource_location import KNOWN_APP_STATES

    row = await _record(AppActionBinding, binding_id)
    if row is None:
        raise ResourceNotFoundError(message="Action binding not found")
    location = await locate_resource(
        "app", required_node_text(row, "app_id"), allowed_app_states=KNOWN_APP_STATES
    )
    roots = await get_default_context().database.find(
        "edge",
        {"target": binding_id, "entity": HAS_ACTION_BINDING._entity_name()},
        limit=2,
    )
    if (
        location is None
        or location.workspace_id != required_node_text(row, "workspace_id")
        or len(roots) != 1
        or roots[0].get("source") != location.app_id
    ):
        raise AuthorizationUnavailableError()
    return location.workspace_id


@_current_authority_read
async def app_delegation_roots(app_id: str) -> list[Any]:
    """Collect App-owned action records inside an authorized cascade unit."""
    from app.services.resource_location import KNOWN_APP_STATES

    location = await locate_resource("app", app_id, allowed_app_states=KNOWN_APP_STATES)
    if location is None:
        raise AuthorizationUnavailableError()
    database = get_default_context().database
    rows = await database.find(
        "node",
        {"entity": AppActionBinding._entity_name(), "context.app_id": app_id},
        limit=1025,
    )
    if len(rows) > 1024:
        raise AuthorizationUnavailableError()
    roots = []
    for row in rows:
        if required_node_text(row, "workspace_id") != location.workspace_id:
            raise AuthorizationUnavailableError()
        edges = await database.find(
            "edge",
            {"target": row["id"], "entity": HAS_ACTION_BINDING._entity_name()},
            limit=2,
        )
        if len(edges) != 1 or edges[0].get("source") != app_id:
            raise AuthorizationUnavailableError()
        node = await get_default_context()._deserialize_entity(AppActionBinding, row)
        if node is None:
            raise AuthorizationUnavailableError()
        roots.append(node)
    return roots


async def delete_app_delegation_roots(app_id: str) -> None:
    """Delete reviewed App action records; caller owns current lifecycle authority."""
    for node in await app_delegation_roots(app_id):
        await node.delete(cascade=False)


@_current_authority_read
async def action_role_delegable(
    *, principal_id: str, app_id: str, operation_key: str
) -> bool:
    """Check issuance eligibility; every execution still evaluates current targets and all conditions.

    This is never an operation allow. It only permits attaching a bounded Policy
    whose exact runtime ceiling remains action_decision with the full payload.
    """
    with isolated_permissions_cache(), ordinary_authority_only():
        app, definition = await current_definition(app_id)
        workspace_id = required_node_text(app, "workspace_id")
        user, baseline = await membership_baseline(principal_id, workspace_id)
        if user is None or await resolve_role(user.id, "app", app_id) is None:
            return False
        specs = [
            spec
            for spec in (
                definition["context"]["canonical_manifest"].get("app") or {}
            ).get("operations", [])
            if spec.get("key") == operation_key
        ]
        if len(specs) != 1 or not specs[0].get("authorization"):
            return False
        declaration = ActionAuthorization.model_validate(specs[0]["authorization"])
        walker = AppActionGrantWalker(
            workspace_id=workspace_id,
            app_id=app_id,
            definition_id=definition["id"],
            fingerprint=definition["context"]["manifest_fingerprint"],
            user_id=user.id,
            baseline=baseline,
            roles=declaration.roles,
            targets=[],
        )
        walker._issuance_only = True
        app_node = await App.find_one({"id": app_id})
        if app_node is None:
            raise AuthorizationUnavailableError()
        await walker.spawn(app_node)
        return bool(walker.matched_ids)
