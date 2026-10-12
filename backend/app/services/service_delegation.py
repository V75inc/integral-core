"""A subject administrator cannot delegate authority on foreign targets."""

from typing import Any

from app.middleware.permissions_cache import isolated_permissions_cache
from app.schemas.policy import Resource, Subject
from app.services.permissions import get_user_node
from app.services.policy_registry import _resolve_subject_node
from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource
from app.services.workspace_governance import ordinary_authority_only
from app.services.workspace_permissions import can_access_workspace


async def policy_delegation_allowed(
    policy,
    subject: Subject,
    action: str,
    resource: Resource,
    *,
    payload: dict | None = None,
) -> bool:
    """Intersect the complete matched policy with live issuer and subject scope.

    A wildcard action is a matcher only: this check prices the exact requested
    action against current human authority. Global scope creates no implicit
    cross-workspace privilege. Trusted system jobs use their existing explicit
    system perimeter rather than impersonating a departed human.
    """
    from jvspatial.core.context import get_default_context

    from app.models.nodes import Policy
    from app.services.authority_facts import primary_node_record, required_node_text
    from app.services.policy_engine import _evaluate_human_default

    with isolated_permissions_cache(), ordinary_authority_only():
        row = await primary_node_record(
            get_default_context().database, Policy, policy.id
        )
        if row is None or row["context"].get("is_active") is False:
            return False
        from types import SimpleNamespace

        from app.services.policy_engine import (
            _action_matches,
            _filter_matches,
            _scope_matches,
        )
        from app.services.policy_registry import current_policy_body

        body = current_policy_body(
            row, subject_kind=subject.kind, subject_id=subject.id
        )
        if (
            not body["is_active"]
            or not _scope_matches(body["scope"], resource.scope)
            or not _action_matches(body["actions"], action)
            or not _filter_matches(SimpleNamespace(**body), resource)[0]
        ):
            return False
        if (
            body["requires_human_approval"] != policy.requires_human_approval
            or body["created_by"] != policy.created_by
        ):
            return False
        issuer_id = required_node_text(row, "created_by")
        issuer = await get_user_node(issuer_id)
        if issuer is None:
            return False
        actor = await _resolve_subject_node(subject.kind, subject.id)
        if actor is None:
            return False
        actor_row = await primary_node_record(
            get_default_context().database, type(actor), actor.id
        )
        if actor_row is None:
            return False
        from app.models.edges import HAS_POLICY

        roots = await get_default_context().database.find(
            "edge", {"target": policy.id, "entity": HAS_POLICY._entity_name()}, limit=2
        )
        if len(roots) != 1 or roots[0].get("source") != actor.id:
            from app.exceptions import AuthorizationUnavailableError

            raise AuthorizationUnavailableError()
        if not await _subject_contract(subject, actor_row):
            return False
        actor_body = actor_row["context"]
        if actor_body.get("is_active") is not True and subject.kind == "agent":
            return False
        if (
            subject.kind == "connector"
            and action in {"connector.sync", "connector.read", "tool.invoke"}
            and resource.kind == "connector"
        ):
            if resource.id != actor.id or resource.scope != f"connector:{actor.id}":
                return False
            owner = await get_user_node(actor_body.get("owner", ""))
            if owner is None or issuer.id != owner.id:
                return False
            home = actor_body.get("workspace_id")
            if home and await can_access_workspace(issuer.id, home) == "none":
                return False
            return await _evaluate_human_default(action, resource, issuer.id)
        kind, identity = resource.kind, resource.id
        if not identity:
            parts = resource.scope.split(":", 1)
            if len(parts) != 2:
                return False
            kind, identity = parts
        if kind not in {"workspace", "app", "track", "entry", "view"}:
            # Non-Core data/credential subjects need a dedicated bound adapter;
            # an owner string cannot grant their unknown target contracts.
            return False
        location = await locate_resource(
            kind, identity, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if location is None:
            return False
        if await can_access_workspace(issuer.id, location.workspace_id) == "none":
            return False
        home = actor_body.get("workspace_id")
        if home:
            if home != location.workspace_id:
                return False
        elif subject.kind == "agent":
            owner = actor_body.get("user_id")
            owner_node = (
                await get_user_node(owner) if isinstance(owner, str) and owner else None
            )
            if owner_node is None or owner_node.id != issuer.id:
                return False
        else:
            owner = actor_body.get("owner")
            owner_node = (
                await get_user_node(owner) if isinstance(owner, str) and owner else None
            )
            if owner_node is None or owner_node.id != issuer.id:
                return False
        if subject.kind == "connector" and action in {
            "entry.create",
            "entry.update",
            "entry.delete",
        }:
            from app.models.edges import IS_CONNECTED_TO

            track_id = location.track_id or (identity if kind == "track" else "")
            if not track_id or body["scope"] != f"track:{track_id}":
                return False
            bindings = await get_default_context().database.find(
                "edge",
                {
                    "entity": IS_CONNECTED_TO._entity_name(),
                    "source": actor.id,
                    "target": track_id,
                },
                limit=2,
            )
            if len(bindings) != 1:
                return False
        if str(action).startswith("app:"):
            from app.services.access_delegation import action_decision

            bound_app, operation_key = action[4:].rsplit(":", 1)
            if bound_app != location.app_id:
                return False
            return (
                await action_decision(
                    principal_id=issuer.id,
                    app_id=bound_app,
                    operation_key=operation_key,
                    payload=payload,
                )
            ).allowed
        return await _evaluate_human_default(action, resource, issuer.id)


async def validate_policy_issuance(
    *, issuer_id: str, scope: str, actions: list[str], subject: Subject | None = None
) -> None:
    """No unbounded target issuance; scope/action authority stays live at use."""
    from app.api.errors import BadRequestError, InsufficientPermissionsError
    from app.services.policy_engine import _evaluate_human_default

    if scope == "*":
        raise BadRequestError(message="Delegation requires one explicit target scope")
    parts = scope.split(":", 1)
    if (
        len(parts) != 2
        or parts[0] not in {"workspace", "app", "track", "entry"}
        or not parts[1]
    ):
        raise BadRequestError(
            message="Delegation scope must identify a canonical target"
        )
    location = await locate_resource(
        parts[0], parts[1], allowed_app_states=MANAGEMENT_APP_STATES
    )
    if (
        location is None
        or await can_access_workspace(issuer_id, location.workspace_id) == "none"
    ):
        raise InsufficientPermissionsError(message="Target authority required")
    if subject is not None and subject.kind in {"agent", "connector"}:
        from jvspatial.core.context import get_default_context

        from app.services.authority_facts import primary_node_record

        node = await _resolve_subject_node(subject.kind, subject.id)
        row = (
            await primary_node_record(
                get_default_context().database, type(node), node.id
            )
            if node
            else None
        )
        if row is None or not await _subject_contract(subject, row):
            raise InsufficientPermissionsError(
                message="Current subject attachment required"
            )
        if (
            row["context"].get("workspace_id")
            and row["context"]["workspace_id"] != location.workspace_id
        ):
            raise InsufficientPermissionsError(
                message="Target exceeds subject workspace"
            )
        if row["context"].get("app_id") and row["context"]["app_id"] != location.app_id:
            raise InsufficientPermissionsError(message="Target exceeds subject App")
    with isolated_permissions_cache(), ordinary_authority_only():
        for action in actions:
            if (
                not isinstance(action, str)
                or not action
                or action == "*"
                or action.endswith(".*")
            ):
                raise BadRequestError(
                    message="New delegation requires exact action names"
                )
            if action.startswith("app:"):
                from app.services.access_delegation import action_role_delegable

                try:
                    bound_app, operation_key = action[4:].rsplit(":", 1)
                except ValueError as exc:
                    raise BadRequestError(message="Invalid App action") from exc
                allowed = (
                    parts[0] == "app"
                    and bound_app == parts[1]
                    and await action_role_delegable(
                        principal_id=issuer_id,
                        app_id=bound_app,
                        operation_key=operation_key,
                    )
                )
            else:
                allowed = await _evaluate_human_default(
                    action, Resource(kind=parts[0], id=parts[1], scope=scope), issuer_id
                )
            if not allowed:
                raise InsufficientPermissionsError(
                    message="Action exceeds current delegator authority"
                )


async def delegated_principal_ids(subject: Subject) -> tuple[str, ...]:
    """Return exact current human policy issuers for identity fencing.

    This enumerates candidate issuers and is never itself an allow. The effect
    boundary must evaluate a complete current policy and its exact target after
    locking these identities and the workspace in canonical order.
    """
    from jvspatial.core.context import get_default_context

    from app.exceptions import AuthorizationUnavailableError
    from app.models.edges import HAS_POLICY
    from app.models.nodes import Policy
    from app.services.authority_facts import primary_node_record, required_node_text

    if subject.kind == "human":
        user = await get_user_node(subject.id)
        if user is None:
            raise AuthorizationUnavailableError()
        return (user.id,)
    if subject.kind not in {"agent", "connector"}:
        raise AuthorizationUnavailableError()
    node = await _resolve_subject_node(subject.kind, subject.id)
    if node is None:
        raise AuthorizationUnavailableError()
    database = get_default_context().database
    row = await primary_node_record(database, type(node), node.id)
    if row is None or (
        subject.kind == "agent" and row["context"].get("is_active") is not True
    ):
        raise AuthorizationUnavailableError()
    edges = await database.find(
        "edge", {"source": node.id, "entity": HAS_POLICY._entity_name()}, limit=257
    )
    if len(edges) > 256:
        raise AuthorizationUnavailableError()
    identities = set()
    for edge in edges:
        policy = await primary_node_record(database, Policy, edge.get("target", ""))
        if policy is None:
            raise AuthorizationUnavailableError()
        if policy["context"].get("is_active") is not True:
            continue
        issuer = await get_user_node(required_node_text(policy, "created_by"))
        if issuer is None:
            raise AuthorizationUnavailableError()
        identities.add(issuer.id)
    return tuple(sorted(identities))


async def delegated_subject_record(subject: Subject) -> tuple[type, str, str]:
    """Resolve the exact service Node whose primary lifecycle row is fenced."""
    from jvspatial.core.context import get_default_context

    from app.exceptions import AuthorizationUnavailableError
    from app.services.authority_facts import primary_node_record

    if subject.kind not in {"agent", "connector"}:
        raise AuthorizationUnavailableError()
    node = await _resolve_subject_node(subject.kind, subject.id)
    if node is None:
        raise AuthorizationUnavailableError()
    row = await primary_node_record(get_default_context().database, type(node), node.id)
    if row is None or (
        subject.kind == "agent" and row["context"].get("is_active") is not True
    ):
        raise AuthorizationUnavailableError()
    if not await _subject_contract(subject, row):
        raise AuthorizationUnavailableError()
    return type(node), "node", node.id


async def _subject_contract(subject: Subject, row: dict) -> bool:
    """Require the complete named service attachment before trusting caches."""
    from jvspatial.core.context import get_default_context

    from app.agentive.edges import HAS_AGENT_CONFIG, HAS_ORG_AGENT, OWNS
    from app.models.edges import CONTAINS, HAS_CONNECTOR

    body = row["context"]
    database = get_default_context().database
    if subject.kind == "agent":
        if body.get("facet") and body.get("scope") and body["facet"] != body["scope"]:
            return False
        if body.get("app_id"):
            edge, parent = CONTAINS, body["app_id"]
            location = await locate_resource("app", parent)
            if location is None or (
                body.get("workspace_id")
                and body["workspace_id"] != location.workspace_id
            ):
                return False
        elif body.get("scope") == "personal":
            owner = await get_user_node(body.get("user_id", ""))
            if owner is None:
                return False
            edge, parent = HAS_AGENT_CONFIG, owner.id
        elif body.get("scope") == "org_facing":
            edge, parent = HAS_ORG_AGENT, body.get("workspace_id")
            if (
                not isinstance(parent, str)
                or not parent
                or await locate_resource("workspace", parent) is None
            ):
                return False
        else:
            return False
        rows = await database.find(
            "edge", {"target": row["id"], "entity": edge._entity_name()}, limit=2
        )
        return len(rows) == 1 and rows[0].get("source") == parent
    owner = await get_user_node(body.get("owner", ""))
    if owner is None:
        return False
    owners = await database.find(
        "edge", {"target": row["id"], "entity": OWNS._entity_name()}, limit=2
    )
    if len(owners) != 1 or owners[0].get("source") != owner.id:
        return False
    home = body.get("workspace_id")
    rows = await database.find(
        "edge", {"target": row["id"], "entity": HAS_CONNECTOR._entity_name()}, limit=2
    )
    if not home:
        return not rows
    if not isinstance(home, str) or await locate_resource("workspace", home) is None:
        return False
    return len(rows) == 1 and rows[0].get("source") == home


def policy_api_mutation(function):
    """Hold current actor/subject/target authority through a Policy API effect."""
    import inspect
    from functools import wraps

    signature = inspect.signature(function)

    @wraps(function)
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        from contextlib import nullcontext

        from jvspatial.core.context import get_default_context

        from app.api.errors import BadRequestError
        from app.api.utils import resolve_principal_id
        from app.models.nodes import Policy
        from app.services.app_operations.transaction_scope import (
            graph_transaction_available,
            postgres_graph_transaction,
        )
        from app.services.authority_facts import primary_node_record
        from app.services.identity_admission import identity_admission
        from app.services.sharing_write_scope import sharing_write_scope

        values = signature.bind(*args, **kwargs)
        values.apply_defaults()
        request = values.arguments["request"]
        actor_id = resolve_principal_id(request)
        policy_id = values.arguments.get("policy_id")
        row = (
            await primary_node_record(get_default_context().database, Policy, policy_id)
            if policy_id
            else None
        )
        if policy_id and row is None:
            return await function(*args, **kwargs)
        body = row["context"] if row else values.arguments
        subject_kind, subject_id = body["subject_kind"], body["subject_id"]
        node = await _resolve_subject_node(subject_kind, subject_id)
        scope = values.arguments.get("scope") or body.get("scope", "*")
        location = None
        parts = scope.split(":", 1)
        if len(parts) == 2 and parts[0] in {"workspace", "app", "track", "entry"}:
            location = await locate_resource(
                parts[0], parts[1], allowed_app_states=MANAGEMENT_APP_STATES
            )
        if location is None and node is not None and subject_kind != "human":
            subject_row = await primary_node_record(
                get_default_context().database, type(node), node.id
            )
            if subject_row:
                if subject_row["context"].get("app_id"):
                    location = await locate_resource(
                        "app",
                        subject_row["context"]["app_id"],
                        allowed_app_states=MANAGEMENT_APP_STATES,
                    )
                elif subject_row["context"].get("workspace_id"):
                    location = await locate_resource(
                        "workspace", subject_row["context"]["workspace_id"]
                    )
                elif subject_row["context"].get("scope") == "personal" and subject_row[
                    "context"
                ].get("user_id"):
                    from app.services.personal_workspace import _find_personal_workspace

                    owner = await get_user_node(subject_row["context"]["user_id"])
                    personal = await _find_personal_workspace(owner) if owner else None
                    if personal:
                        location = await locate_resource("workspace", personal.id)
        if row and values.arguments.get("scope") is not None:
            old_parts = body.get("scope", "").split(":", 1)
            old_location = (
                await locate_resource(
                    old_parts[0], old_parts[1], allowed_app_states=MANAGEMENT_APP_STATES
                )
                if len(old_parts) == 2
                and old_parts[0] in {"workspace", "app", "track", "entry"}
                else None
            )
            if (
                old_location
                and location
                and old_location.workspace_id != location.workspace_id
            ):
                raise BadRequestError(
                    message="Cross-workspace retarget requires a new Policy"
                )
        participants = (
            (actor_id, node.id)
            if subject_kind == "human" and node is not None
            else (actor_id,)
        )
        database = get_default_context().database
        transaction_scope = (
            postgres_graph_transaction(database)
            if graph_transaction_available(database)
            else nullcontext()
        )
        async with (
            transaction_scope,
            identity_admission(participants, active_principal_ids=(actor_id,)),
        ):
            database = get_default_context().database
            if graph_transaction_available(database):
                if node is not None and subject_kind != "human":
                    locked = await database.find_one_and_update(
                        "node",
                        {"id": node.id, "entity": type(node)._entity_name()},
                        {"$set": {}},
                    )
                    if locked is None:
                        from app.exceptions import AuthorizationUnavailableError

                        raise AuthorizationUnavailableError()
                if policy_id:
                    locked = await database.find_one_and_update(
                        "node",
                        {"id": policy_id, "entity": Policy._entity_name()},
                        {"$set": {}},
                    )
                    if locked is None:
                        from app.exceptions import AuthorizationUnavailableError

                        raise AuthorizationUnavailableError()
            if location is not None:
                from app.services.request_scope import require_effect_target_scope

                await require_effect_target_scope(
                    request, actor_id, location.workspace_id
                )
                if row is not None:
                    current_policy = await primary_node_record(
                        database, Policy, policy_id
                    )
                    if (
                        current_policy is None
                        or current_policy["context"].get("scope")
                        != row["context"].get("scope")
                        or current_policy["context"].get("subject_id") != subject_id
                        or current_policy["context"].get("subject_kind") != subject_kind
                    ):
                        from app.exceptions import AuthorizationUnavailableError

                        raise AuthorizationUnavailableError()
                async with sharing_write_scope(
                    location.workspace_id,
                    principal_ids=participants,
                    active_principal_ids=(actor_id,),
                ):
                    return await function(*args, **kwargs)
            return await function(*args, **kwargs)

    return invoke


async def emit_policy_change(*, policy, revision: int, **event) -> None:
    """Retain the canonical audit vocabulary with same-unit durable intent."""
    from jvspatial.core.context import get_default_context

    from app.services.change_event import emit_change_event
    from app.services.entry_write_scope import is_postgres_entry_transaction
    from app.services.sharing_write_scope import emit_sharing_change

    # Known target/home grants participate in the shared outbox. Inert legacy
    # records without a canonical workspace have no target authority; their
    # operator cleanup remains a separate maintenance disposition.
    if is_postgres_entry_transaction(get_default_context().database):
        from app.services.sharing_write_scope import _active

        if _active.get() is not None:
            await emit_sharing_change(
                clock={"id": policy.id, "context": {"revision": revision}},
                emitter=emit_change_event,
                **event,
            )
            return
    if is_postgres_entry_transaction(get_default_context().database):
        from app.exceptions import AuthorizationUnavailableError

        raise AuthorizationUnavailableError()
    await emit_change_event(**event)
