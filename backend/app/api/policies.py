"""Admin REST surface for Policy CRUD (POL-02 + POL-04).

Plan 03-03 Task 3. Plan 06-05 rewrite uses jvspatial's ``@endpoint`` decorator
+ ``JVSpatialAPIException`` subclasses (no more raw FastAPI ``APIRouter`` +
``HTTPException``):

  - ``@endpoint`` from ``jvspatial.api`` for all 5 routes (POST/GET-list/GET-one
    /PATCH/DELETE) per CLAUDE.md § jvspatial Object-Spatial Contract.
  - Pydantic body validation with ``model_config={"extra":"forbid"}``
    enforced by the schemas in ``app/schemas/policy.py``.
  - ``resolve_principal_id(request)`` derives the authenticated principal —
    NEVER trust a client-supplied ``created_by``.
  - Canonical 5-key error envelope via ``app.api.errors`` (jvspatial-aligned).
  - 404-as-not-found on cross-user access (T-03-03-I01 enumeration
    mitigation; same pattern as Phase 1 connectors.py T-01-04-02).
  - ``emit_change_event`` after every mutation (Phase 2 D-05 single emission
    path — the AST grep gate at ``tests/test_change_event_no_bypass.py``
    enforces this CI-side).
  - ``policy_decision_clear_for_subject`` on every mutation (D-11 cache
    invalidation — the just-modified subject's cached evaluate() decisions
    become stale within the request lifetime).
  - Dogfoods ``policy_engine.evaluate`` with ``policy.create`` /
    ``policy.read`` / ``policy.update`` / ``policy.delete`` actions — this
    module IS the canonical example Plan 04 (MCP adapter) will consult when
    deriving the ``policy_action`` tool descriptor field per CRUD verb.

Per CONTEXT D-02: registered ALWAYS (not gated on ``AGENTIVE_ENABLED``) —
admins must be able to attach a Policy to a User for compliance even when
the agentive layer is off. Registration is via the standard side-effect
import in ``backend/app/api/__init__.py``; ``main.py`` no longer needs an
explicit ``include_router`` call for policies after the 06-05 migration.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import Request, status
from jvspatial.api import endpoint

from app.api.errors import (
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import is_platform_admin, resolve_principal_id
from app.middleware.permissions_cache import policy_decision_clear_for_subject
from app.models.nodes import Policy
from app.schemas.policy import (
    ActorKind,
    ExplainActionRequest,
    ExplainActionResponse,
    PolicyResponse,
    Resource,
    Subject,
)
from app.services.change_event import emit_change_event
from app.services.permissions import can_admin_app
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.policy_registry import (
    create_policy,
    delete_policy,
    get_policy,
    list_policies_for_subject,
    update_policy,
)
from app.services.workspace_permissions import is_workspace_admin_or_owner

logger = logging.getLogger(__name__)


async def _caller_administers_subject(
    request: Request, user_id: str, subject_kind: str, subject_id: str
) -> bool:
    """True when the caller may attach / read / modify Policies for a subject.

    ``policy_engine.evaluate`` allows every authenticated human on the
    ``policy.*`` actions (default-human path), so this is the authorization
    that actually binds: a platform admin administers every subject; a
    non-admin administers only the agents and connectors they own —
    a personal-scope AgentConfig whose ``user_id`` is the caller, an
    org-facing AgentConfig in a workspace the caller owns / administers, an
    App-bundled AgentConfig whose App the caller administers, or a Connector
    the caller owns. ``human`` and ``system`` subjects (and subjects that do
    not resolve to a node) are admin-only.
    """
    kind = str(subject_kind or "").strip().lower()
    admin = is_platform_admin(request)
    if admin:
        logger.warning(
            "policy.administers: platform admin short-circuit kind=%r subject=%r roles=%r",
            kind,
            subject_id,
            getattr(getattr(request.state, "user", None), "roles", None),
        )
        return True
    if kind == "agent":
        try:
            from app.agentive.nodes import AgentConfig
        except ImportError:
            return False
        config = await AgentConfig.get(subject_id) if subject_id else None
        if config is None:
            return False
        app_id = getattr(config, "app_id", None)
        if app_id and await can_admin_app(user_id, app_id):
            return True
        scope = getattr(config, "facet", None) or getattr(config, "scope", "")
        if scope == "personal":
            return bool(config.user_id) and config.user_id == user_id
        if scope == "org_facing":
            workspace_id = getattr(config, "workspace_id", None)
            return bool(workspace_id) and await is_workspace_admin_or_owner(
                user_id, workspace_id
            )
        return False
    if kind == "connector":
        try:
            from app.agentive.nodes import Connector
        except ImportError:
            return False
        connector = await Connector.get(subject_id) if subject_id else None
        if connector is None:
            return False
        owner = getattr(connector, "owner", "") or ""
        if owner and owner == user_id:
            return True
        # Owner scalar may be empty when the OWNS edge was the only write;
        # fall back to edge walk so create-time User.get misses do not open
        # the subject to every caller.
        try:
            from app.agentive.edges import Owns

            owners = await connector.nodes(edge=[Owns], node=["User"], direction="in")
        except Exception:
            owners = []
        for o in owners or []:
            oid = str(getattr(o, "user_id", "") or "") or str(
                getattr(o, "id", "") or ""
            )
            if oid and oid == user_id:
                return True
        return False
    return False


def _to_response(p: Policy) -> PolicyResponse:
    """Project a Policy Node into the wire response shape."""
    # ``subject_kind`` on the Node is stored as ``str`` (Phase 2 ChangeEvent.actor_kind
    # idiom — see app/models/nodes.py Policy docstring); PolicyResponse.subject_kind
    # is the ActorKind Literal. The conversion is safe because every write goes
    # through PolicyCreate which already enforces the Literal at the API edge.
    return PolicyResponse(
        id=p.id,
        subject_kind=p.subject_kind,  # type: ignore[arg-type]
        subject_id=p.subject_id,
        scope=p.scope,
        actions=p.actions,
        entry_types=p.entry_types,
        tags=p.tags,
        requires_human_approval=p.requires_human_approval,
        is_active=p.is_active,
        created_at=p.created_at,
        updated_at=p.updated_at,
        created_by=p.created_by,
    )


def _snapshot(p: Policy) -> Dict[str, Any]:
    """Snapshot a Policy for the audit log (before / after envelope payload)."""
    return _to_response(p).model_dump(mode="json")


@endpoint(
    "/policies",
    methods=["POST"],
    auth=True,
    tags=["Policies"],
    status_code=status.HTTP_201_CREATED,
)
async def post_create_policy(
    request: Request,
    subject_id: str = "",
    subject_kind: ActorKind = "agent",
    scope: str = "*",
    actions: Optional[List[str]] = None,
    entry_types: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    requires_human_approval: bool = False,
    is_active: bool = True,
) -> PolicyResponse:
    """Create a Policy. ``created_by`` derives from the authenticated principal.

    Dogfood: ``policy_engine.evaluate`` is consulted with
    ``action="policy.create"`` before persistence — under Plan 01's
    default-human path an authenticated user is allowed (Phase 7 SSO/SAML will
    tighten this; per CONTEXT D-15 T-01 the v1 single-tenant disposition is
    accept-with-audit-trail).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="policy.create",
        resource=Resource(
            kind="policy",
            id="*",  # not yet created
            scope=f"user:{user_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="policy.create denied",
            details={"decision_reason": decision.reason},
        )
    if not await _caller_administers_subject(
        request, user_id, subject_kind, subject_id
    ):
        raise InsufficientPermissionsError(
            message="policy.create denied — caller does not administer subject",
            details={"subject_kind": subject_kind, "subject_id": subject_id},
        )

    # NOTE: human/system subjects are admin-only via _caller_administers_subject.
    p = await create_policy(
        subject_kind=subject_kind,
        subject_id=subject_id,
        scope=scope,
        actions=list(actions or []),
        entry_types=list(entry_types or []),
        tags=list(tags or []),
        requires_human_approval=requires_human_approval,
        is_active=is_active,
        created_by=user_id,
    )

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="policy.create",
        resource_type="Policy",
        resource_id=p.id,
        before=None,
        after=_snapshot(p),
        scope=f"user:{user_id}",
    )

    # D-11 cache invalidation — just-attached subject's cached decisions stale.
    policy_decision_clear_for_subject(p.subject_kind, p.subject_id)

    return _to_response(p)


@endpoint(
    "/policies",
    methods=["GET"],
    auth=True,
    tags=["Policies"],
)
async def list_policies(
    request: Request,
    subject_kind: Optional[str] = None,
    subject_id: Optional[str] = None,
) -> List[PolicyResponse]:
    """List Policies. Filter by ``subject_kind`` + ``subject_id`` when both supplied;
    otherwise list all Policies visible to the caller.

    Default-human path (today) returns all rows for any authenticated user
    (CONTEXT D-15 T-01 v1 disposition). Future Phase 7 SSO/SAML role-gating
    will narrow this via an explicit ``policy.read`` Policy on admin roles.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="policy.read",
        resource=Resource(
            kind="policy",
            id="*",
            scope=f"user:{user_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="policy.read denied",
            details={"decision_reason": decision.reason},
        )

    if subject_kind and subject_id:
        results = await list_policies_for_subject(subject_kind, subject_id)
    else:
        results = list(await Policy.find())

    # Only policies whose subject the caller administers (admins see all).
    administered: Dict[tuple, bool] = {}
    visible: List[Policy] = []
    for p in results:
        key = (p.subject_kind, p.subject_id)
        if key not in administered:
            administered[key] = await _caller_administers_subject(
                request, user_id, p.subject_kind, p.subject_id
            )
        if administered[key]:
            visible.append(p)

    return [_to_response(p) for p in visible]


@endpoint(
    "/policies/{policy_id}",
    methods=["GET"],
    auth=True,
    tags=["Policies"],
)
async def get_policy_by_id(
    request: Request,
    policy_id: str,
) -> PolicyResponse:
    """Fetch a Policy by id.

    Returns 404 for unknown id AND for a Policy the caller has no policy.read
    permission on (T-03-03-I01 enumeration mitigation — both branches return
    the same canonical envelope so callers cannot probe for valid ids by
    observing 403-vs-404 status code differences).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    p = await get_policy(policy_id)
    if p is None:
        raise ResourceNotFoundError(
            message=f"Policy {policy_id!r} not found",
            details={"policy_id": policy_id},
        )

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="policy.read",
        resource=Resource(
            kind="policy",
            id=p.id,
            scope=f"user:{p.created_by or user_id}",
        ),
    )
    if not decision.allowed or not await _caller_administers_subject(
        request, user_id, p.subject_kind, p.subject_id
    ):
        # T-03-03-I01 — return 404 (not 403) to avoid existence-leak.
        raise ResourceNotFoundError(
            message=f"Policy {policy_id!r} not found",
            details={"policy_id": policy_id},
        )

    return _to_response(p)


@endpoint(
    "/policies/{policy_id}",
    methods=["PATCH"],
    auth=True,
    tags=["Policies"],
)
async def patch_policy(
    request: Request,
    policy_id: str,
    scope: Optional[str] = None,
    actions: Optional[List[str]] = None,
    entry_types: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    requires_human_approval: Optional[bool] = None,
    is_active: Optional[bool] = None,
) -> PolicyResponse:
    """Partial-update a Policy. Cache is cleared on success.

    PATCH cannot retarget ``subject_kind`` / ``subject_id`` / ``created_by`` —
    those are absent from ``PolicyUpdate``. Callers must delete + re-create to
    re-target.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    existing = await get_policy(policy_id)
    if existing is None:
        raise ResourceNotFoundError(
            message=f"Policy {policy_id!r} not found",
            details={"policy_id": policy_id},
        )

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="policy.update",
        resource=Resource(
            kind="policy",
            id=existing.id,
            scope=f"user:{existing.created_by or user_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="policy.update denied",
            details={"decision_reason": decision.reason},
        )
    if not await _caller_administers_subject(
        request, user_id, existing.subject_kind, existing.subject_id
    ):
        raise InsufficientPermissionsError(
            message="policy.update denied — caller does not administer subject",
            details={"policy_id": policy_id},
        )

    before_snap = _snapshot(existing)
    updated = await update_policy(
        policy_id,
        scope=scope,
        actions=actions,
        entry_types=entry_types,
        tags=tags,
        requires_human_approval=requires_human_approval,
        is_active=is_active,
    )
    if updated is None:
        # Defensive — should be unreachable given the prior existence check.
        raise ResourceNotFoundError(
            message=f"Policy {policy_id!r} not found",
            details={"policy_id": policy_id},
        )

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="policy.update",
        resource_type="Policy",
        resource_id=updated.id,
        before=before_snap,
        after=_snapshot(updated),
        scope=f"user:{user_id}",
    )

    policy_decision_clear_for_subject(updated.subject_kind, updated.subject_id)

    return _to_response(updated)


@endpoint(
    "/policies/{policy_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Policies"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_policy_by_id(
    request: Request,
    policy_id: str,
):
    """Delete a Policy. Cache is cleared on success."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    existing = await get_policy(policy_id)
    if existing is None:
        raise ResourceNotFoundError(
            message=f"Policy {policy_id!r} not found",
            details={"policy_id": policy_id},
        )

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="policy.delete",
        resource=Resource(
            kind="policy",
            id=existing.id,
            scope=f"user:{existing.created_by or user_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="policy.delete denied",
            details={"decision_reason": decision.reason},
        )
    if not await _caller_administers_subject(
        request, user_id, existing.subject_kind, existing.subject_id
    ):
        raise InsufficientPermissionsError(
            message="policy.delete denied — caller does not administer subject",
            details={"policy_id": policy_id},
        )

    before_snap = _snapshot(existing)
    subject_kind = existing.subject_kind
    subject_id = existing.subject_id
    deleted = await delete_policy(policy_id)
    if not deleted:
        # Defensive — should be unreachable.
        raise ResourceNotFoundError(
            message=f"Policy {policy_id!r} not found",
            details={"policy_id": policy_id},
        )

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="policy.delete",
        resource_type="Policy",
        resource_id=policy_id,
        before=before_snap,
        after=None,
        scope=f"user:{user_id}",
    )

    policy_decision_clear_for_subject(subject_kind, subject_id)


@endpoint("/policies/explain", methods=["POST"], auth=True, tags=["Policies"])
async def explain_action(
    request: Request,
    subject_id: str = "",
    subject_kind: ActorKind = "human",
    action: str = "",
    resource_kind: str = "entry",
    resource_id: str = "",
    resource_scope: str = "",
    entry_type: Optional[str] = None,
    tags: Optional[List[str]] = None,
    operation_key: Optional[str] = None,
    app_id: Optional[str] = None,
) -> ExplainActionResponse:
    """F1 forensic read: explain allow/deny for a subject/action/resource.

    Caller must be platform admin, workspace admin/owner for ``app_id`` (when
    provided), or the explained human subject themselves. Does not mutate
    state and does not emit ChangeEvents (dry evaluate via the engine).

    Body fields are flat (jvspatial ParameterModelFactory) — same shape as
    ``ExplainActionRequest`` in ``app.schemas.policy``.
    """
    # Validate via the schema so enums / required fields stay authoritative.
    body = ExplainActionRequest(
        subject_kind=subject_kind,
        subject_id=subject_id,
        action=action,
        resource_kind=resource_kind,  # type: ignore[arg-type]
        resource_id=resource_id,
        resource_scope=resource_scope,
        entry_type=entry_type,
        tags=list(tags or []),
        operation_key=operation_key,
        app_id=app_id,
    )

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    explaining_self = body.subject_kind == "human" and body.subject_id == user_id
    allowed_caller = explaining_self or is_platform_admin(request)
    if not allowed_caller and body.app_id:
        try:
            allowed_caller = await can_admin_app(user_id, body.app_id)
        except Exception:  # noqa: BLE001
            allowed_caller = False
    if not allowed_caller:
        # Workspace-admin path when scope encodes app:/org:
        scope = (body.resource_scope or "").strip()
        if scope.startswith("app:"):
            try:
                allowed_caller = await can_admin_app(user_id, scope.split(":", 1)[1])
            except Exception:  # noqa: BLE001
                allowed_caller = False
        elif scope.startswith("org:") or scope.startswith("workspace:"):
            ws_id = scope.split(":", 1)[1]
            try:
                allowed_caller = await is_workspace_admin_or_owner(user_id, ws_id)
            except Exception:  # noqa: BLE001
                allowed_caller = False
    if not allowed_caller:
        raise InsufficientPermissionsError(
            message="explain denied — caller cannot forensically inspect this subject",
        )

    decision = await policy_evaluate(
        subject=Subject(kind=body.subject_kind, id=body.subject_id),
        action=body.action,
        resource=Resource(
            kind=body.resource_kind,
            id=body.resource_id,
            scope=body.resource_scope,
            entry_type=body.entry_type,
            tags=list(body.tags or []),
        ),
        _dry_run=True,
    )

    access_snapshot: Optional[Dict[str, Any]] = None
    rk = str(body.resource_kind)
    if rk in ("app", "track", "entry") and body.resource_id:
        try:
            from app.services.sharing import list_access

            # Snapshot is best-effort for the *caller* (who already passed
            # admin gates) — shows inheritance context around the resource.
            access_snapshot = await list_access(user_id, rk, body.resource_id)
        except Exception:  # noqa: BLE001
            logger.debug("explain_action: list_access failed", exc_info=True)

    return ExplainActionResponse(
        allowed=bool(decision.allowed),
        reason=str(decision.reason or ""),
        matched_policy_id=decision.matched_policy_id,
        policy_chain=list(decision.policy_chain or []),
        approval_id=decision.approval_id,
        operation_key=body.operation_key,
        access_snapshot=access_snapshot,
    )
