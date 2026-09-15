"""Connector Node REST surface (CON-01 + AGT-04).

Phase 1: ``POST /api/agentive/connectors`` (create) and
``GET /api/agentive/connectors/{id}`` (read).

Phase 8 Plan 08-02 Task 1: extends the surface with the three CRUD routes
the Phase 5 sync stack has been missing since v1 close (RESEARCH Pitfall 4):

- ``GET /api/agentive/connectors`` — LIST (owner-scoped, per-row policy gate)
- ``PATCH /api/agentive/connectors/{id}`` — partial update
- ``DELETE /api/agentive/connectors/{id}`` — hard delete (204)

Phase 8 Plan 08-02 Task 3: appends IS_CONNECTED_TO Track binding routes
(POST/GET/DELETE under ``/agentive/connectors/{id}/bindings``).

All endpoints require an authenticated principal — either a user JWT or the
service-auth signed envelope (Plan 01-02 hardened middleware enforces D-01).

Routes are auto-registered via ``@endpoint`` from ``jvspatial.api`` (side-effect
import in ``app/agentive/__init__.py``); the agentive surface is only mounted
when ``AGENTIVE_ENABLED=1`` at process start (D-08, app/main.py).
"""

import logging
from typing import Any, Dict, List, Optional, cast

from fastapi import Request, status
from jvspatial.api import endpoint

from app.agentive.connectors.mcp_client import describe_mcp_failure
from app.agentive.services.connector_registry_node import (
    attach_connector_to_workspace,
    create_connector,
    decrypt_auth_state,
)
from app.agentive.services.connector_registry_node import (
    delete_connector as svc_delete_connector,
)
from app.agentive.services.connector_registry_node import (
    encrypt_auth_state,
    get_connector,
    list_connectors_for_owner,
)
from app.agentive.services.connector_registry_node import (
    update_connector as svc_update_connector,
)
from app.agentive.types import AgentType
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.middleware.permissions_cache import policy_decision_clear_for_subject
from app.models.edges import IS_CONNECTED_TO
from app.models.nodes import Track
from app.schemas.agentive.connectors import (
    CatalogEntry,
    CatalogInstallRequest,
    CatalogInstallResponse,
    CatalogListResponse,
    ConnectorListResponse,
    ConnectorResponse,
    McpConnectorHealthResponse,
    McpMountResponse,
    McpReauthorizeResponse,
    McpRegistryEntry,
    McpRegistryMountPreview,
    McpRegistrySearchResponse,
    MountMcpConnectorRequest,
    MountMcpFromRegistryRequest,
    UpdateConnectorRequest,
    redact_auth_state,
)
from app.schemas.audit import ChangeEventAction
from app.schemas.connector_bindings import (
    ConnectorBindingListResponse,
    ConnectorBindingResponse,
    CreateConnectorBindingRequest,
)
from app.schemas.policy import Resource, Subject
from app.services.change_event import emit_change_event
from app.services.policy_engine import evaluate as policy_evaluate

logger = logging.getLogger(__name__)


def _derive_conflict_policy(subclass_slug: str) -> Optional[str]:
    """Read ``conflict_policy`` from the SyncConnector subclass registry.

    Returns ``None`` when no slug is set or the slug is unregistered. Per A5 of
    Phase 8 planning context: this is the canonical read-only derivation —
    ``conflict_policy`` is NEVER persisted on the Connector Node.
    """
    if not subclass_slug:
        return None
    try:
        from app.services.connectors.registry import get_sync_connector

        instance = get_sync_connector(subclass_slug)
        return getattr(instance, "conflict_policy", "manual_resolve")
    except Exception:
        return None


def _audit_snapshot(connector: Any) -> Dict[str, Any]:
    """W3 revision — minimal Connector snapshot safe for ChangeEvent logging.

    Per T-08-02-I03 mitigation: ``auth_state`` is EXPLICITLY EXCLUDED from
    before/after snapshots because it can carry bearer tokens / OAuth refresh
    tokens / API keys. This helper is used by Task 1 PATCH/DELETE handlers
    AND by Task 3 bind/unbind handlers — single redaction surface.
    """
    return {
        "kind": getattr(connector, "kind", None),
        "subclass_slug": getattr(connector, "subclass_slug", "") or "",
        "sync_interval_seconds": int(
            getattr(connector, "sync_interval_seconds", 300) or 300
        ),
        "capabilities": list(getattr(connector, "capabilities", []) or []),
        "permissions": list(getattr(connector, "permissions", []) or []),
        # auth_state DELIBERATELY OMITTED — see T-08-02-I03 threat model entry.
    }


def _to_response(c: Any) -> ConnectorResponse:
    """Project a Connector Node into the wire response shape.

    Phase 8 Plan 08-02 — surfaces the Phase-5 scalars (``subclass_slug``,
    ``sync_interval_seconds``, ``last_synced_at``) plus the derived
    ``conflict_policy`` field (A5 — read-only from the SyncConnector subclass
    registry). ADR-009 — MCP connectors redact ``env``/``headers`` secrets.
    """
    from app.schemas.agentive.connectors import mcp_safe_auth_state

    subclass_slug = getattr(c, "subclass_slug", "") or ""
    raw_auth = dict(getattr(c, "auth_state", None) or {})
    if subclass_slug == "mcp" or getattr(c, "kind", None) == "mcp":
        # ADR-005 — redact env/headers value maps (keys stay for UX).
        safe_auth = mcp_safe_auth_state(raw_auth)
        # Discovered tools persist on auth_state (Connector has no such field).
        if not safe_auth.get("discovered_tools"):
            node_tools = getattr(c, "discovered_tools", None)
            if isinstance(node_tools, list):
                safe_auth["discovered_tools"] = node_tools
    elif subclass_slug == "quickbooks":
        from app.schemas.agentive.quickbooks import quickbooks_safe_auth_state

        safe_auth = quickbooks_safe_auth_state(raw_auth)
    elif subclass_slug == "gmail":
        from app.schemas.agentive.gmail import gmail_safe_auth_state

        safe_auth = gmail_safe_auth_state(raw_auth)
    else:
        # OAuth / API-key material never leaves the server.
        safe_auth = redact_auth_state(c.auth_state)
    return ConnectorResponse(
        id=c.id,
        kind=c.kind,
        owner=c.owner,
        auth_state=safe_auth,
        sync_cursor=c.sync_cursor,
        mapping_profile=c.mapping_profile,
        permissions=c.permissions,
        capabilities=c.capabilities,
        conflict_policy=_derive_conflict_policy(subclass_slug),
        subclass_slug=subclass_slug or None,
        sync_interval_seconds=int(getattr(c, "sync_interval_seconds", 300) or 300),
        last_synced_at=getattr(c, "last_synced_at", None),
        workspace_id=getattr(c, "workspace_id", None) or None,
        health_status=getattr(c, "health_status", None) or None,
        last_error=getattr(c, "last_error", None),
        last_health_at=getattr(c, "last_health_at", None),
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@endpoint("/agentive/connectors", methods=["POST"], auth=True, tags=["Agentive"])
async def post_create_connector(
    request: Request,
    kind: AgentType = "jvagent",
    auth_state: Optional[Dict[str, Any]] = None,
    sync_cursor: Optional[str] = None,
    mapping_profile: Optional[str] = None,
    permissions: Optional[List[str]] = None,
    capabilities: Optional[List[str]] = None,
) -> ConnectorResponse:
    """Create a Connector. ``owner`` derives from the authenticated principal (D-07)."""
    user = getattr(request.state, "user", None)
    if not user:
        raise MissingAuthenticationError(message="Authentication required")
    owner_id = getattr(user, "id", None) or ""
    if not owner_id:
        raise MissingAuthenticationError(message="Authenticated user has no id")

    c = await create_connector(
        owner=owner_id,
        kind=kind,
        auth_state=dict(auth_state or {}),
        sync_cursor=sync_cursor,
        mapping_profile=mapping_profile,
        permissions=list(permissions or []),
        capabilities=list(capabilities or []),
    )

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    # T-08-02-I03: ``_audit_snapshot`` — never the full node, whose
    # ``auth_state`` may carry tokens — into the ChangeEvent log.
    await emit_change_event(
        actor_kind="human",
        actor_id=owner_id,
        action="connector.create",
        resource_type="Connector",
        resource_id=c.id,
        before=None,
        after=_audit_snapshot(c),
        scope=f"user:{owner_id}",
    )

    return _to_response(c)


@endpoint(
    "/agentive/connectors/catalog",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def list_connector_catalog_endpoint(request: Request) -> CatalogListResponse:
    """List vetted in-repo connector packages.

    Registered before ``/connectors/{id}`` so ``catalog`` is not captured
    as a connector id.
    """
    from app.connectors.catalog_loader import load_catalog

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await _require_workspace_admin_for_mcp_registry(request, user_id)

    entries = [_catalog_entry_wire(e) for e in load_catalog()]
    return CatalogListResponse(entries=entries, total=len(entries))


@endpoint(
    "/agentive/connectors/catalog/{slug}",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_connector_catalog_entry_endpoint(
    request: Request, slug: str
) -> CatalogEntry:
    """Install preview for one vetted catalog package (no secrets)."""
    from app.connectors.catalog_loader import get_catalog_entry

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await _require_workspace_admin_for_mcp_registry(request, user_id)

    try:
        entry = get_catalog_entry(slug)
    except KeyError:
        raise ResourceNotFoundError(message=f"Unknown catalog slug: {slug!r}")
    except ValueError as e:
        raise BadRequestError(message=str(e))
    return _catalog_entry_wire(entry)


@endpoint(
    "/agentive/connectors/catalog/{slug}/install",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def install_connector_from_catalog_endpoint(
    request: Request, slug: str
) -> CatalogInstallResponse:
    """Install a vetted catalog package into the active workspace."""
    from app.connectors.catalog_loader import get_catalog_entry

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await _require_workspace_admin_for_mcp_registry(request, user_id)

    try:
        entry = get_catalog_entry(slug)
    except KeyError:
        raise ResourceNotFoundError(message=f"Unknown catalog slug: {slug!r}")
    except ValueError as e:
        raise BadRequestError(message=str(e))

    try:
        raw_body = await request.json()
    except Exception:  # noqa: BLE001
        raw_body = {}
    if not isinstance(raw_body, dict):
        raise BadRequestError(message="Request body must be a JSON object")
    try:
        body = CatalogInstallRequest.model_validate(raw_body)
    except Exception as e:  # noqa: BLE001
        raise BadRequestError(
            message="Invalid catalog install body",
            details={"validation": str(e)},
        )

    secrets = dict(body.secrets or {})
    auth_type = entry["auth"]["type"]

    if entry["slug"] == "quickbooks_mcp":
        return await _catalog_install_quickbooks_mcp_oauth(
            user_id=user_id,
            workspace_id=workspace_id,
            entry=entry,
            secrets=secrets,
        )

    if auth_type == "oauth2" and entry["kind"] == "mcp":
        return await _catalog_install_mcp_oauth(
            user_id=user_id,
            workspace_id=workspace_id,
            entry=entry,
            secrets=secrets,
        )

    if auth_type == "oauth2":
        return await _catalog_install_oauth(
            user_id=user_id,
            workspace_id=workspace_id,
            entry=entry,
            secrets=secrets,
        )

    try:
        if entry["kind"] == "mcp":
            connector = await _catalog_install_mcp(
                user_id=user_id,
                workspace_id=workspace_id,
                entry=entry,
                secrets=secrets,
            )
        else:
            connector = await _catalog_install_sync(
                user_id=user_id,
                workspace_id=workspace_id,
                entry=entry,
                secrets=secrets,
            )
    except ValueError as e:
        raise BadRequestError(message=str(e))

    from app.agentive.connectors.mcp_oauth import (
        OAUTH_STATUS_PENDING,
        authorization_url_for_oauth,
    )

    oauth = dict((getattr(connector, "auth_state", None) or {}).get("oauth") or {})
    if oauth.get("status") == OAUTH_STATUS_PENDING and oauth.get("state"):

        return CatalogInstallResponse(
            action="oauth",
            slug=entry["slug"],
            consent_url=authorization_url_for_oauth(oauth, oauth["state"]),
            state=str(oauth.get("state") or ""),
            connector=_to_response(connector),
        )
    return CatalogInstallResponse(
        action="created",
        slug=entry["slug"],
        connector=_to_response(connector),
    )


@endpoint(
    "/agentive/connectors/{connector_id}",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_connector_by_id(
    request: Request,
    connector_id: str,
) -> ConnectorResponse:
    """Fetch a Connector by id. Returns 404 for unknown id OR cross-user access.

    T-01-04-02 mitigation: both branches return the same canonical envelope so
    a non-owner cannot probe for connector ids by observing different status
    codes / error_code strings.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise MissingAuthenticationError(message="Authentication required")

    c = await get_connector(connector_id)
    if c is None:
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )

    # Phase 1 simple ownership check — the canonical owner relationship is the
    # Owns edge per D-07, but the scalar `owner` field is the authoritative
    # check here until policy_engine.evaluate (Phase 3) takes over.
    owner_id = getattr(user, "id", None) or ""
    if c.owner != owner_id:
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )
    return _to_response(c)


# =====================================================================
# Phase 8 Plan 08-02 Task 1 — LIST / PATCH / DELETE (RESEARCH Pitfall 4)
# =====================================================================


@endpoint(
    "/agentive/connectors",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def list_connectors(
    request: Request,
) -> ConnectorListResponse:
    """List Connectors owned by the caller and mounted in the active workspace.

    Per-row ``policy_engine.evaluate(action="connector.read")`` gate; denied
    rows are silently dropped from BOTH the array AND the ``total`` field —
    no enumeration leak (T-08-02-I02; mirrors T-06-02-01 idiom).

    Ownership alone was the whole filter, which made this list answer the wrong
    question for the product it sits in. An MCP connector carries a
    ``workspace_id`` and registers its tools into THAT workspace, so a user who
    mounts connectors in two workspaces saw both lists interleaved with no
    workspace label on the row — and switching workspaces changed nothing.
    Rows without a workspace (native sync connectors, pre-ADR-009 records) are
    legitimately workspace-less and still show.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    from app.services.request_scope import resolve_workspace_id_from_request

    active_workspace_id = await resolve_workspace_id_from_request(request, user_id)
    all_owned = await list_connectors_for_owner(user_id)
    visible: List[ConnectorResponse] = []
    for c in all_owned:
        row_workspace = (getattr(c, "workspace_id", "") or "").strip()
        if (
            row_workspace
            and active_workspace_id
            and row_workspace != active_workspace_id
        ):
            continue
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="connector.read",
            resource=Resource(
                kind="connector",
                id=c.id,
                scope=f"user:{c.owner or user_id}",
            ),
        )
        if not decision.allowed:
            # T-08-02-I02 — silent drop (no count leak in `total`).
            continue
        visible.append(_to_response(c))

    return ConnectorListResponse(connectors=visible, total=len(visible))


@endpoint(
    "/agentive/connectors/{connector_id}",
    methods=["PATCH"],
    auth=True,
    tags=["Agentive"],
)
async def patch_connector(
    request: Request,
    connector_id: str,
) -> ConnectorResponse:
    """Partial-update a Connector. 404 on unknown id OR cross-owner.

    Body: ``UpdateConnectorRequest`` (Pydantic ``extra: forbid`` — forged
    ``owner`` / ``kind`` injection is blocked at the schema boundary per
    T-08-02-S01). Emits a single ``connector.update`` ChangeEvent with
    redacted snapshots (W3 — ``auth_state`` is never logged).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    existing = await get_connector(connector_id)
    if existing is None or existing.owner != user_id:
        # T-08-02-I01 — same canonical envelope for unknown vs cross-owner.
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )

    # F-9: PATCH rewrites ``auth_state`` — including ``url`` and the OAuth
    # ``token_endpoint`` the next health / refresh / invoke will use — so it
    # needs the same workspace authority as the other lifecycle routes. The
    # owner check above is a scalar that survives removal from the workspace,
    # and the policy below evaluates ``user:<owner>``, which self-allows.
    await _require_connector_workspace_authority(user_id, existing)

    # Body parse + extra:forbid validation (mirrors conflicts.py:111-118 idiom).
    try:
        raw_body = await request.json()
    except Exception:  # noqa: BLE001
        raw_body = {}
    if not isinstance(raw_body, dict):
        raise BadRequestError(message="Request body must be a JSON object")
    try:
        body = UpdateConnectorRequest.model_validate(raw_body)
    except Exception as e:  # noqa: BLE001
        raise BadRequestError(
            message="Invalid PATCH body",
            details={"validation": str(e)},
        )

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="connector.update",
        resource=Resource(
            kind="connector",
            id=existing.id,
            scope=f"user:{existing.owner or user_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="connector.update denied",
            details={"decision_reason": decision.reason},
        )

    before_snap = _audit_snapshot(existing)
    try:
        updated = await svc_update_connector(
            connector_id,
            subclass_slug=body.subclass_slug,
            sync_interval_seconds=body.sync_interval_seconds,
            auth_state=body.auth_state,
            mapping_profile=body.mapping_profile,
            permissions=body.permissions,
            capabilities=body.capabilities,
        )
    except ValueError as e:
        # Round-tripped redacted auth_state — a client bug, not a server one.
        raise BadRequestError(message=str(e))
    if updated is None:
        # Defensive — should be unreachable given the prior existence check.
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )

    # D-05 single emission path. W3 — _audit_snapshot redacts auth_state.
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.update",
        resource_type="Connector",
        resource_id=updated.id,
        before=before_snap,
        after=_audit_snapshot(updated),
        scope=f"user:{user_id}",
    )

    # D-11 cache invalidation — connector identity may be a Policy subject.
    policy_decision_clear_for_subject("connector", updated.id)

    return _to_response(updated)


@endpoint(
    "/agentive/connectors/{connector_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Agentive"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_connector_by_id(
    request: Request,
    connector_id: str,
) -> None:
    """Hard-delete a Connector. 204 on success; 404 on unknown OR cross-owner."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    existing = await get_connector(connector_id)
    if existing is None or existing.owner != user_id:
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )

    await _require_connector_workspace_authority(user_id, existing)

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="connector.delete",
        resource=Resource(
            kind="connector",
            id=existing.id,
            scope=f"user:{existing.owner or user_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="connector.delete denied",
            details={"decision_reason": decision.reason},
        )

    before_snap = _audit_snapshot(existing)
    # ADR-009 — drop workspace tool registrations before deleting the Node.
    subclass = getattr(existing, "subclass_slug", "") or ""
    if subclass == "mcp" or getattr(existing, "kind", None) == "mcp":
        from app.agentive.connectors.mcp_mount import unmount_mcp_connector
        from app.connectors.stdio_env import purge_token_store_for_auth_state

        # #17 — the stdio token store holds a live client secret + refresh
        # token on disk. Nothing deleted it, so every deleted QuickBooks MCP
        # connector left its credentials behind under backend/.data/.
        try:
            purge_token_store_for_auth_state(
                decrypt_auth_state(getattr(existing, "auth_state", None) or {})
            )
        except Exception:  # noqa: BLE001 — never block the delete on cleanup
            logger.exception("connectors: token-store purge failed for %s", existing.id)
        deleted = await unmount_mcp_connector(existing)
    else:
        deleted = await svc_delete_connector(connector_id)
    if not deleted:
        # Defensive — should be unreachable.
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )

    # D-05 single emission path. W3 — _audit_snapshot redacts auth_state.
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.delete",
        resource_type="Connector",
        resource_id=connector_id,
        before=before_snap,
        after=None,
        scope=f"user:{user_id}",
    )

    policy_decision_clear_for_subject("connector", connector_id)
    return None


# =====================================================================
# ADR-009 — MCP client mount / refresh / health
# =====================================================================


def _freeform_stdio_mounts_allowed() -> bool:
    """Free-form stdio RCE surface — only for local pytest (TESTING=1).

    Delegates to the spawn-side gate so there is ONE implementation of this
    decision. Two copies of a gate that governs arbitrary command execution is
    exactly how the surfaces drift apart — the mount endpoint was already
    bypassable via generic create -> PATCH -> /health, which is why the
    authoritative check now lives at the spawn (S-MCP-RCE).
    """
    from app.agentive.connectors.mcp_client import _freeform_stdio_allowed

    return _freeform_stdio_allowed()


@endpoint(
    "/agentive/connectors/mcp/mount",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def mount_mcp_connector_endpoint(request: Request) -> McpMountResponse:
    """Mount an external MCP server: discover tools + hot-register (no restart).

    Requires workspace admin/owner (``X-Integral-Scope: ws:<id>``). Free-form
    ``stdio`` mounts are rejected outside ``TESTING=1`` — use catalog/registry
    paths which supply curated commands. HTTP mounts go through the OAuth-aware
    adapter and return ``McpMountResponse`` (``auth_required`` + consent URL).
    """
    from app.agentive.connectors.mcp_oauth import (
        OAUTH_STATUS_PENDING,
        authorization_url_for_oauth,
    )
    from app.api.errors import ConnectorAuthError
    from app.services.url_safety import validate_outbound_http_url

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await _require_workspace_admin_for_mcp_registry(request, user_id)

    try:
        raw_body = await request.json()
    except Exception:  # noqa: BLE001
        raw_body = {}
    if not isinstance(raw_body, dict):
        raise BadRequestError(message="Request body must be a JSON object")
    try:
        body = MountMcpConnectorRequest.model_validate(raw_body)
    except Exception as e:  # noqa: BLE001
        raise BadRequestError(
            message="Invalid mount body",
            details={"validation": str(e)},
        )

    transport = (body.transport or "stdio").strip().lower()

    try:
        if transport == "stdio":
            if not _freeform_stdio_mounts_allowed():
                raise BadRequestError(
                    message=(
                        "Free-form stdio MCP mounts are disabled; install from "
                        "the connector catalog or MCP registry instead"
                    ),
                    details={"reason": "stdio_forbidden"},
                )
            from app.agentive.connectors.mcp_mount import (
                mount_mcp_connector as mount_stdio,
            )

            connector = await mount_stdio(
                owner_id=user_id,
                workspace_id=workspace_id,
                transport="stdio",
                command=body.command,
                args=list(body.args or []),
                env=body.env,
                display_name=body.display_name,
                registry_name=body.registry_name,
                registry_version=body.registry_version,
            )
            # I-GRAPH-01 / ADR-009 §4 — the mount helper wires User —OWNS→
            # Connector only; without this the node hangs off no Workspace.
            await attach_connector_to_workspace(connector, workspace_id)
            status_lit: str = "mounted"
            consent_url = None
        elif transport == "streamable_http":
            url = (body.url or "").strip()
            if not url:
                raise BadRequestError(message="streamable_http mount requires url")
            await validate_outbound_http_url(url)
            from app.agentive.connectors.mcp_adapter import (
                mount_mcp_connector as mount_http,
            )

            connector = await mount_http(
                owner_id=user_id,
                workspace_id=workspace_id,
                url=url,
                headers=body.headers,
            )
            oauth = dict(
                (getattr(connector, "auth_state", None) or {}).get("oauth") or {}
            )
            if oauth.get("status") == OAUTH_STATUS_PENDING and oauth.get("state"):
                status_lit = "auth_required"
                consent_url = authorization_url_for_oauth(oauth, str(oauth["state"]))
            else:
                status_lit = "mounted"
                consent_url = None
        else:
            raise BadRequestError(message=f"unsupported transport: {transport!r}")
    except BadRequestError:
        raise
    except ConnectorAuthError:
        raise
    except Exception as e:  # noqa: BLE001
        # url_safety raises app.exceptions.BadRequestError (distinct class).
        from app.exceptions import BadRequestError as AppBadRequestError

        if isinstance(e, AppBadRequestError):
            raise BadRequestError(
                message=e.message, details=getattr(e, "details", None)
            )
        if isinstance(e, ValueError):
            raise BadRequestError(message=str(e))
        detail = describe_mcp_failure(e)
        raise BadRequestError(
            message=f"MCP mount failed: {detail}",
            details={"reason": detail},
        )

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.create",
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after=_audit_snapshot(connector),
        scope=f"user:{user_id}",
        details={"via": "mcp_mount", "workspace_id": workspace_id},
    )
    return McpMountResponse(
        status=status_lit,  # type: ignore[arg-type]
        connector=_to_response(connector),
        authorization_url=consent_url,
    )


@endpoint(
    "/agentive/connectors/{connector_id}/mcp/refresh",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def refresh_mcp_connector_endpoint(
    request: Request,
    connector_id: str,
) -> ConnectorResponse:
    """Re-discover remote tools and re-register into the workspace (no restart)."""
    from app.agentive.connectors.mcp_mount import refresh_mcp_connector

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    existing = await get_connector(connector_id)
    if existing is None or existing.owner != user_id:
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )
    if (getattr(existing, "subclass_slug", "") or "") != "mcp":
        raise BadRequestError(message="Connector is not an MCP mount")
    await _require_connector_workspace_authority(user_id, existing)

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="connector.update",
        resource=Resource(
            kind="connector",
            id=existing.id,
            scope=f"connector:{existing.id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="connector.update denied",
            details={"decision_reason": decision.reason},
        )

    before_snap = _audit_snapshot(existing)
    try:
        updated = await refresh_mcp_connector(existing)
    except Exception as e:  # noqa: BLE001
        # Health fields already persisted as error; still return the node.
        detail = describe_mcp_failure(e)
        raise BadRequestError(
            message=f"MCP refresh failed: {detail}",
            details={"reason": detail},
        )

    # D-05 single emission path. A refresh re-discovers remote tools and
    # re-registers them into the workspace, which is a mutation -- this
    # handler already policy-gates it as ``connector.update`` above, so the
    # audit trail carries the same action. W3 -- _audit_snapshot redacts
    # auth_state.
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.update",
        resource_type="Connector",
        resource_id=updated.id,
        before=before_snap,
        after=_audit_snapshot(updated),
        scope=f"user:{user_id}",
    )

    return _to_response(updated)


@endpoint(
    "/agentive/connectors/{connector_id}/mcp/reauthorize",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def reauthorize_mcp_connector_endpoint(
    request: Request,
    connector_id: str,
) -> McpReauthorizeResponse:
    """Restart OAuth on an EXISTING MCP connector.

    When a refresh token dies, ``_refresh_oauth_if_needed`` gives up and
    ``mcp_oauth`` sets ``details.reauth_required``. Until this route existed the
    only recovery was to install from the catalog again, which mints a NEW
    connector node and leaves the dead one behind with its stale tool
    registrations — so every re-connect accumulated a duplicate. This
    re-runs the authorization against the connector already mounted, so its
    id, workspace edge and tool registrations survive.

    Two OAuth shapes are supported, matching install:

    * a catalog entry with an ``oauth:`` block (Google) → pre-registered client
    * anything else → 401 probe, PRM/AS discovery and DCR
    """
    from app.agentive.connectors.mcp_adapter import MCP_TRANSPORT
    from app.agentive.connectors.mcp_oauth import (
        OAUTH_STATUS_PENDING,
        authorization_url_for_oauth,
        google_oauth_client_credentials,
        mcp_oauth_redirect_uri,
        probe_mcp_authorization,
        sign_mcp_oauth_state,
        start_mcp_oauth_session,
        start_pre_registered_oauth_session,
    )

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    existing = await get_connector(connector_id)
    if existing is None or existing.owner != user_id:
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )
    if (getattr(existing, "subclass_slug", "") or "") != "mcp":
        raise BadRequestError(message="Connector is not an MCP mount")
    await _require_connector_workspace_authority(user_id, existing)

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="connector.update",
        resource=Resource(
            kind="connector",
            id=existing.id,
            scope=f"connector:{existing.id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="connector.update denied",
            details={"decision_reason": decision.reason},
        )

    auth = decrypt_auth_state(getattr(existing, "auth_state", None) or {})
    if (auth.get("transport") or MCP_TRANSPORT) != MCP_TRANSPORT:
        raise BadRequestError(
            message=(
                "Only HTTP MCP connectors re-authorize here; a stdio mount "
                "re-authorizes through its own provider flow"
            )
        )
    url = str(auth.get("url") or "").strip()
    if not url:
        raise BadRequestError(message="Connector has no MCP url to re-authorize")

    before_snap = _audit_snapshot(existing)
    catalog_slug = str(auth.get("catalog_slug") or "").strip()
    oauth_cfg: Dict[str, Any] = {}
    if catalog_slug:
        from app.connectors.catalog_loader import get_catalog_entry

        try:
            entry = get_catalog_entry(catalog_slug)
        except KeyError:
            entry = {}
        raw_cfg = entry.get("oauth")
        oauth_cfg = raw_cfg if isinstance(raw_cfg, dict) else {}

    try:
        if oauth_cfg.get("authorization_endpoint"):
            env_id, env_secret = google_oauth_client_credentials()
            prior = auth.get("oauth") if isinstance(auth.get("oauth"), dict) else {}
            client_id = str(prior.get("client_id") or "").strip() or env_id
            client_secret = str(prior.get("client_secret") or "").strip() or env_secret
            if not client_id or not client_secret:
                raise BadRequestError(
                    message=(
                        "This connector's OAuth client credentials are no "
                        "longer available; re-install it from the catalog."
                    )
                )
            oauth = start_pre_registered_oauth_session(
                redirect_uri=mcp_oauth_redirect_uri(),
                client_id=client_id,
                client_secret=client_secret,
                authorization_endpoint=str(oauth_cfg["authorization_endpoint"]),
                token_endpoint=str(oauth_cfg["token_endpoint"]),
                scopes=list(oauth_cfg.get("scopes") or []),
                extra_authorize_params=dict(
                    oauth_cfg.get("extra_authorize_params") or {}
                ),
                token_endpoint_auth_method=str(
                    oauth_cfg.get("token_endpoint_auth_method") or "client_secret_post"
                ),
            )
        else:
            probe = await probe_mcp_authorization(url)
            if not probe.needs_oauth:
                raise BadRequestError(
                    message=(
                        "This MCP server did not ask for authorization, so "
                        "there is nothing to re-authorize. Try Health or "
                        "Refresh instead."
                    )
                )
            oauth = await start_mcp_oauth_session(
                url,
                probe_response=probe.response,
                redirect_uri=mcp_oauth_redirect_uri(),
            )
    except BadRequestError:
        raise
    except Exception as e:  # noqa: BLE001
        detail = describe_mcp_failure(e)
        raise BadRequestError(
            message=f"MCP re-authorization failed: {detail}",
            details={"reason": detail},
        )

    state = sign_mcp_oauth_state(user_id, existing.id)
    pending = dict(oauth)
    pending["state"] = state
    pending["status"] = OAUTH_STATUS_PENDING
    auth["oauth"] = pending
    existing.auth_state = encrypt_auth_state(auth)
    await existing.save()

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.update",
        resource_type="Connector",
        resource_id=existing.id,
        before=before_snap,
        after=_audit_snapshot(existing),
        scope=f"user:{user_id}",
        details={"via": "mcp_reauthorize"},
    )
    return McpReauthorizeResponse(
        connector_id=existing.id,
        consent_url=authorization_url_for_oauth(pending, state),
        state=state,
    )


@endpoint(
    "/agentive/connectors/{connector_id}/health",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_mcp_connector_health(
    request: Request,
    connector_id: str,
) -> McpConnectorHealthResponse:
    """Probe MCP connector health (list_tools round-trip) and persist status."""
    from app.agentive.connectors.mcp_mount import health_check_mcp_connector

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    existing = await get_connector(connector_id)
    if existing is None or existing.owner != user_id:
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )

    await _require_connector_workspace_authority(user_id, existing)

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="connector.read",
        resource=Resource(
            kind="connector",
            id=existing.id,
            scope=f"connector:{existing.id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="connector.read denied",
            details={"decision_reason": decision.reason},
        )

    if (getattr(existing, "subclass_slug", "") or "") != "mcp":
        return McpConnectorHealthResponse(
            connector_id=existing.id,
            status=getattr(existing, "health_status", None) or "unknown",
            last_error=getattr(existing, "last_error", None),
            last_health_at=getattr(existing, "last_health_at", None),
            tool_count=0,
        )

    result = await health_check_mcp_connector(existing)
    return McpConnectorHealthResponse(**result)


def _catalog_entry_wire(entry: Dict[str, Any]) -> CatalogEntry:
    return CatalogEntry.model_validate(
        {
            "slug": entry["slug"],
            "display_name": entry["display_name"],
            "description": entry.get("description") or "",
            "category": entry["category"],
            "kind": entry["kind"],
            "icon": entry["icon"],
            "vetted": True,
            "auth": entry["auth"],
            "transport": entry.get("transport"),
            "url": entry.get("url"),
            "command": entry.get("command"),
            "args": list(entry.get("args") or []),
        }
    )


async def _catalog_install_oauth(
    *,
    user_id: str,
    workspace_id: str,
    entry: Dict[str, Any],
    secrets: Dict[str, str],
) -> CatalogInstallResponse:
    slug = entry["slug"]
    collected = _collect_catalog_secrets(entry, secrets)
    client_id = (collected.get("client_id") or "").strip()
    if slug == "gmail":
        from app.services.connectors.gmail_oauth import (
            _env_client_id as env_oauth_client_id,
        )
        from app.services.connectors.gmail_oauth import (
            build_consent_url,
        )
    elif slug == "quickbooks":
        from app.services.connectors.quickbooks_oauth import (
            _env_client_id as env_oauth_client_id,
        )
        from app.services.connectors.quickbooks_oauth import (
            build_consent_url,
        )
    else:
        raise BadRequestError(message=f"No OAuth handler for catalog slug {slug!r}")
    if not client_id and not env_oauth_client_id():
        raise BadRequestError(
            message=(
                f"{entry['display_name']} Client ID is required. Enter it in "
                "the install form, or set the matching environment variable "
                "on the server."
            )
        )
    auth_state: Dict[str, Any] = {
        "catalog_slug": slug,
        "display_name": entry["display_name"],
        **collected,
    }
    connector = await create_connector(
        owner=user_id,
        kind="jvagent",
        auth_state=auth_state,
        capabilities=["connector.sync"],
        workspace_id=workspace_id,
    )
    impl = (entry.get("implementation") or {}).get("sync_connector_class") or slug
    connector.subclass_slug = impl
    connector.workspace_id = workspace_id
    await connector.save()
    url, state = build_consent_url(
        user_id,
        client_id=client_id or None,
        connector_id=connector.id,
    )

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.create",
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after=_audit_snapshot(connector),
        scope=f"user:{user_id}",
        details={"via": "catalog_install", "slug": slug},
    )
    return CatalogInstallResponse(
        action="oauth",
        slug=slug,
        consent_url=url,
        state=state,
        connector=_to_response(connector),
    )


async def _catalog_install_quickbooks_mcp_oauth(
    *,
    user_id: str,
    workspace_id: str,
    entry: Dict[str, Any],
    secrets: Dict[str, str],
) -> CatalogInstallResponse:
    """Start Intuit OAuth, then spawn the local MCP server after callback."""
    from app.services.connectors.quickbooks_oauth import (
        _env_client_id as env_oauth_client_id,
    )
    from app.services.connectors.quickbooks_oauth import (
        build_consent_url,
    )

    collected = _collect_catalog_secrets(entry, secrets)
    client_id = (collected.get("client_id") or "").strip()
    if not client_id and not env_oauth_client_id():
        raise BadRequestError(
            message=(
                f"{entry['display_name']} Client ID is required. Enter it in "
                "the install form, or set QUICKBOOKS_CLIENT_ID on the server."
            )
        )
    # ``collected`` carries the operator-entered ``client_secret`` from
    # quickbooks_mcp.yaml. It is needed again at callback time (Intuit's token
    # exchange), so it stays on ``auth_state`` — but encrypted at rest (F-7),
    # and the MCP allowlist redactor keeps it off every wire response (F-5).
    auth_state: Dict[str, Any] = {
        "catalog_slug": entry["slug"],
        "display_name": entry["display_name"],
        "transport": "stdio",
        "command": entry.get("command") or "",
        "args": list(entry.get("args") or []),
        "env_defaults": dict(entry.get("env_defaults") or {}),
        **collected,
    }
    connector = await create_connector(
        owner=user_id,
        kind="mcp",
        auth_state=auth_state,
        capabilities=["mcp.client"],
        skip_default_policy=True,
        workspace_id=workspace_id,
    )
    connector.subclass_slug = "mcp"
    connector.workspace_id = workspace_id
    connector.health_status = "unknown"
    await connector.save()
    url, state = build_consent_url(
        user_id,
        client_id=client_id or None,
        connector_id=connector.id,
    )

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.create",
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after=_audit_snapshot(connector),
        scope=f"user:{user_id}",
        details={"via": "catalog_install", "slug": entry["slug"]},
    )
    return CatalogInstallResponse(
        action="oauth",
        slug=entry["slug"],
        consent_url=url,
        state=state,
        connector=_to_response(connector),
    )


async def _finish_quickbooks_mcp_oauth(
    connector: Any,
    *,
    refresh_token: str,
    realm_id: str,
) -> Any:
    """Write the Intuit token file and discover stdio MCP tools."""
    from app.agentive.connectors.mcp_mount import complete_stdio_mount
    from app.connectors.catalog_loader import get_catalog_entry
    from app.connectors.stdio_env import (
        persist_quickbooks_token_store,
        spawn_env_from_quickbooks_oauth,
    )

    auth_state = decrypt_auth_state(getattr(connector, "auth_state", None) or {})
    try:
        entry = get_catalog_entry("quickbooks_mcp")
    except KeyError:
        entry = {}
    spawn = spawn_env_from_quickbooks_oauth(
        auth_state=auth_state,
        refresh_token=refresh_token,
        realm_id=realm_id,
        env_defaults=dict(
            entry.get("env_defaults") or auth_state.get("env_defaults") or {}
        ),
    )
    workspace_id = getattr(connector, "workspace_id", "") or ""
    _path, spawn_env, persist_env = persist_quickbooks_token_store(
        workspace_id=workspace_id,
        env=spawn,
    )
    command = str(auth_state.get("command") or entry.get("command") or "").strip()
    args = list(auth_state.get("args") or entry.get("args") or [])
    connector = await complete_stdio_mount(
        connector,
        command=command,
        args=args,
        env=spawn_env,
        display_name=str(
            auth_state.get("display_name") or entry.get("display_name") or ""
        ),
        catalog_slug="quickbooks_mcp",
    )
    auth = dict(getattr(connector, "auth_state", None) or {})
    auth["env"] = dict(persist_env)
    connector.auth_state = encrypt_auth_state(auth)
    await connector.save()
    return connector


async def _catalog_install_mcp_oauth(
    *,
    user_id: str,
    workspace_id: str,
    entry: Dict[str, Any],
    secrets: Dict[str, str],
) -> CatalogInstallResponse:
    """HTTP MCP + catalog-declared OAuth (Google Drive — no 401 probe)."""
    from app.agentive.connectors.mcp_adapter import begin_pre_registered_mcp_oauth
    from app.agentive.connectors.mcp_oauth import (
        authorization_url_for_oauth,
        google_oauth_client_credentials,
        mcp_oauth_redirect_uri,
        start_pre_registered_oauth_session,
    )

    oauth_cfg = entry.get("oauth") or {}
    if not isinstance(oauth_cfg, dict) or not oauth_cfg.get("authorization_endpoint"):
        raise BadRequestError(
            message=(
                f"{entry['display_name']} is missing catalog OAuth endpoints. "
                "Check the package YAML."
            )
        )
    if (entry.get("transport") or "streamable_http") != "streamable_http":
        raise BadRequestError(
            message="Pre-registered MCP OAuth is only supported for HTTP mounts"
        )
    url = (entry.get("url") or "").strip()
    if not url:
        raise BadRequestError(message="MCP catalog entry is missing url")

    collected = _collect_catalog_secrets(entry, secrets)
    env_id, env_secret = google_oauth_client_credentials()
    client_id = (collected.get("client_id") or "").strip() or env_id
    client_secret = (collected.get("client_secret") or "").strip() or env_secret
    if not client_id:
        raise BadRequestError(
            message=(
                f"{entry['display_name']} Client ID is required. Enter it in "
                "the install form, or set GOOGLE_OAUTH_CLIENT_ID / "
                "GMAIL_OAUTH_CLIENT_ID on the server."
            )
        )
    if not client_secret:
        raise BadRequestError(
            message=(
                f"{entry['display_name']} Client secret is required. Enter it "
                "in the install form, or set GOOGLE_OAUTH_CLIENT_SECRET / "
                "GMAIL_OAUTH_CLIENT_SECRET on the server."
            )
        )

    oauth = start_pre_registered_oauth_session(
        redirect_uri=mcp_oauth_redirect_uri(),
        client_id=client_id,
        client_secret=client_secret,
        authorization_endpoint=str(oauth_cfg["authorization_endpoint"]),
        token_endpoint=str(oauth_cfg["token_endpoint"]),
        scopes=list(oauth_cfg.get("scopes") or []),
        extra_authorize_params=dict(oauth_cfg.get("extra_authorize_params") or {}),
        token_endpoint_auth_method=str(
            oauth_cfg.get("token_endpoint_auth_method") or "client_secret_post"
        ),
    )
    connector = await begin_pre_registered_mcp_oauth(
        owner_id=user_id,
        workspace_id=workspace_id,
        url=url,
        oauth=oauth,
    )
    auth = dict(getattr(connector, "auth_state", None) or {})
    auth["catalog_slug"] = entry["slug"]
    auth["display_name"] = entry["display_name"]
    connector.auth_state = encrypt_auth_state(auth)
    await connector.save()

    pending = dict(decrypt_auth_state(auth).get("oauth") or {})
    state = str(pending.get("state") or "")
    consent_url = authorization_url_for_oauth(pending, state)

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.create",
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after=_audit_snapshot(connector),
        scope=f"user:{user_id}",
        details={"via": "catalog_install", "slug": entry["slug"]},
    )
    return CatalogInstallResponse(
        action="oauth",
        slug=entry["slug"],
        consent_url=consent_url,
        state=state,
        connector=_to_response(connector),
    )


def _collect_catalog_secrets(
    entry: Dict[str, Any], secrets: Dict[str, str]
) -> Dict[str, str]:
    collected: Dict[str, str] = {}
    for field in entry["auth"].get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "")
        if not name:
            continue
        value = str(secrets.get(name) or "").strip()
        if not value and field.get("required", True):
            raise ValueError(f"missing required field: {name}")
        if value:
            collected[name] = value
    return collected


async def _catalog_install_sync(
    *,
    user_id: str,
    workspace_id: str,
    entry: Dict[str, Any],
    secrets: Dict[str, str],
) -> Any:
    collected = _collect_catalog_secrets(entry, secrets)
    impl = (entry.get("implementation") or {}).get("sync_connector_class") or entry[
        "slug"
    ]
    auth_state: Dict[str, Any] = {
        "catalog_slug": entry["slug"],
        "display_name": entry["display_name"],
        **collected,
    }
    connector = await create_connector(
        owner=user_id,
        kind="jvagent",
        auth_state=auth_state,
        capabilities=["connector.sync"],
        workspace_id=workspace_id,
    )
    connector.subclass_slug = impl
    connector.workspace_id = workspace_id
    await connector.save()
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.create",
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after=_audit_snapshot(connector),
        scope=f"user:{user_id}",
        details={"via": "catalog_install", "slug": entry["slug"]},
    )
    return connector


async def _catalog_install_mcp(
    *,
    user_id: str,
    workspace_id: str,
    entry: Dict[str, Any],
    secrets: Dict[str, str],
) -> Any:
    collected = _collect_catalog_secrets(entry, secrets)
    transport = entry.get("transport") or "streamable_http"
    headers: Optional[Dict[str, str]] = None
    env: Optional[Dict[str, str]] = None
    if entry["auth"]["type"] == "headers":
        headers = collected or None
    elif entry["auth"]["type"] in ("env", "api_key"):
        from app.connectors.stdio_env import merge_install_env

        env = merge_install_env(entry, collected) or None

    try:
        if transport == "streamable_http":
            from app.agentive.connectors.mcp_adapter import (
                mount_mcp_connector as mount_http,
            )

            connector = await mount_http(
                owner_id=user_id,
                workspace_id=workspace_id,
                url=entry.get("url") or "",
                headers=headers,
            )
        else:
            from app.agentive.connectors.mcp_mount import mount_mcp_connector

            connector = await mount_mcp_connector(
                owner_id=user_id,
                workspace_id=workspace_id,
                transport=transport,
                command=entry.get("command"),
                args=list(entry.get("args") or []),
                env=env,
                url=entry.get("url"),
                headers=headers,
                display_name=entry["display_name"],
                catalog_slug=entry["slug"],
            )
            # The HTTP branch above wires the workspace edge inside
            # create_connector; the stdio mount helper does not.
            await attach_connector_to_workspace(connector, workspace_id)
    except ValueError as e:
        raise BadRequestError(message=str(e))
    except BadRequestError:
        raise
    except Exception as e:  # noqa: BLE001
        detail = describe_mcp_failure(e)
        raise BadRequestError(
            message=f"MCP catalog mount failed: {detail}",
            details={"reason": detail},
        )

    auth = dict(getattr(connector, "auth_state", None) or {})
    auth["catalog_slug"] = entry["slug"]
    auth["display_name"] = entry["display_name"]
    connector.auth_state = encrypt_auth_state(auth)
    await connector.save()

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.create",
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after=_audit_snapshot(connector),
        scope=f"user:{user_id}",
        details={"via": "catalog_install", "slug": entry["slug"]},
    )
    return connector


async def _require_connector_workspace_authority(user_id: str, connector: Any) -> None:
    """Re-check workspace authority on a connector lifecycle call (F-9).

    Owning a connector was the ONLY check on refresh / health / delete, and
    ownership is a scalar that survives everything: a user removed from an org
    workspace kept calling ``/mcp/refresh`` and kept re-registering remote tool
    specs into a workspace they no longer belong to. Ownership is necessary,
    not sufficient — the workspace the connector is mounted into has to still
    want this caller.

    A workspace-less **MCP** connector is refused outright. Letting those
    through on the owner gate alone left the original door open: any
    authenticated user, with no workspace standing at all, could create an
    ``mcp`` row, PATCH it to ``streamable_http`` with an arbitrary URL and
    headers, and drive server-side outbound requests through ``GET /health``.
    An MCP mount always belongs to a workspace, so a row without one was never
    authorized by anybody.

    Non-MCP rows (generic ``POST /connectors`` records, pre-ADR-009 data) are
    legitimately workspace-less and drive no outbound fetch or spawn, so the
    owner gate stands alone for those.
    """
    workspace_id = (getattr(connector, "workspace_id", "") or "").strip()
    if not workspace_id:
        from app.agentive.connectors.mcp_mount import MCP_SUBCLASS_SLUG

        is_mcp = (getattr(connector, "kind", "") or "") == "mcp" or (
            getattr(connector, "subclass_slug", "") or ""
        ) == MCP_SUBCLASS_SLUG
        if is_mcp:
            raise InsufficientPermissionsError(
                message=(
                    "This MCP connector is not mounted in a workspace; mount "
                    "it through the connector catalog before using it"
                ),
                details={"connector_id": getattr(connector, "id", "")},
            )
        return
    from app.services.workspace_permissions import is_workspace_admin_or_owner

    if not await is_workspace_admin_or_owner(user_id, workspace_id):
        raise InsufficientPermissionsError(
            message=(
                "Workspace admin or owner required — this connector is mounted "
                "in a workspace you no longer administer"
            ),
            details={"workspace_id": workspace_id},
        )


async def _require_workspace_admin_for_mcp_registry(
    request: Request, user_id: str
) -> str:
    """MCP registry browse/mount and free-form mount require workspace admin/owner."""
    from app.services.request_scope import resolve_workspace_id_from_request
    from app.services.workspace_permissions import is_workspace_admin_or_owner

    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    if not workspace_id:
        raise BadRequestError(
            message="X-Integral-Scope workspace required for MCP connector ops"
        )
    if not await is_workspace_admin_or_owner(user_id, workspace_id):
        raise InsufficientPermissionsError(
            message="Workspace admin or owner required for MCP connector ops"
        )
    return workspace_id


@endpoint(
    "/agentive/connectors/mcp/registry/search",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def search_mcp_registry_endpoint(request: Request) -> McpRegistrySearchResponse:
    """Proxy search against the official MCP Registry (cached)."""
    from app.agentive.connectors.mcp_registry_client import search_registry_servers

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await _require_workspace_admin_for_mcp_registry(request, user_id)

    q = request.query_params.get("q", "")
    cursor = request.query_params.get("cursor", "")
    try:
        limit = int(request.query_params.get("limit", "20"))
    except ValueError:
        limit = 20

    try:
        result = await search_registry_servers(q=q, cursor=cursor, limit=limit)
    except ValueError as e:
        raise BadRequestError(message=str(e))

    return McpRegistrySearchResponse.model_validate(result)


def _mcp_registry_name_param(request: Request) -> str:
    """Registry names are slash-delimited (e.g. io.github.org/pkg) — never path params."""
    name = (request.query_params.get("name") or "").strip()
    if not name:
        raise BadRequestError(message="Query parameter 'name' is required")
    return name


@endpoint(
    "/agentive/connectors/mcp/registry/servers",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_mcp_registry_server_endpoint(request: Request) -> McpRegistryEntry:
    """Fetch one official registry server by name (latest version)."""
    from app.agentive.connectors.mcp_registry_client import get_registry_server

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await _require_workspace_admin_for_mcp_registry(request, user_id)
    server_name = _mcp_registry_name_param(request)

    try:
        entry = await get_registry_server(server_name)
    except ValueError as e:
        raise ResourceNotFoundError(message=str(e))

    return McpRegistryEntry.model_validate(entry)


@endpoint(
    "/agentive/connectors/mcp/registry/preview",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def preview_mcp_registry_mount_endpoint(
    request: Request,
) -> McpRegistryMountPreview:
    """Mount preview for a registry entry (no secrets)."""
    from app.agentive.connectors.mcp_registry_client import (
        build_mount_preview,
        get_registry_server,
    )

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await _require_workspace_admin_for_mcp_registry(request, user_id)
    server_name = _mcp_registry_name_param(request)

    try:
        entry = await get_registry_server(server_name)
        preview = build_mount_preview(entry)
    except ValueError as e:
        raise ResourceNotFoundError(message=str(e))

    return McpRegistryMountPreview.model_validate(preview)


@endpoint(
    "/agentive/connectors/mcp/registry/mount",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def mount_mcp_from_registry_endpoint(request: Request) -> ConnectorResponse:
    """Mount an official registry entry into the active workspace."""
    from app.agentive.connectors.mcp_mount import mount_mcp_connector
    from app.agentive.connectors.mcp_registry_client import (
        get_registry_server,
        to_mount_request,
    )

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await _require_workspace_admin_for_mcp_registry(request, user_id)

    try:
        raw_body = await request.json()
    except Exception:  # noqa: BLE001
        raw_body = {}
    if not isinstance(raw_body, dict):
        raise BadRequestError(message="Request body must be a JSON object")
    try:
        body = MountMcpFromRegistryRequest.model_validate(raw_body)
    except Exception as e:  # noqa: BLE001
        raise BadRequestError(
            message="Invalid registry mount body",
            details={"validation": str(e)},
        )

    try:
        entry = await get_registry_server(body.registry_name)
        mount_body = to_mount_request(entry, secrets=body.secrets)
        connector = await mount_mcp_connector(
            owner_id=user_id,
            workspace_id=workspace_id,
            transport=mount_body["transport"],
            command=mount_body.get("command"),
            args=list(mount_body.get("args") or []),
            env=mount_body.get("env"),
            url=mount_body.get("url"),
            headers=mount_body.get("headers"),
            display_name=mount_body.get("display_name"),
            registry_name=mount_body.get("registry_name"),
            registry_version=mount_body.get("registry_version"),
        )
        await attach_connector_to_workspace(connector, workspace_id)
    except ValueError as e:
        raise BadRequestError(message=str(e))
    except Exception as e:  # noqa: BLE001
        detail = describe_mcp_failure(e)
        raise BadRequestError(
            message=f"MCP registry mount failed: {detail}",
            details={"reason": detail},
        )

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.create",
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after=_audit_snapshot(connector),
        scope=f"user:{user_id}",
        details={
            "via": "mcp_registry_mount",
            "workspace_id": workspace_id,
            "registry_name": body.registry_name,
        },
    )
    return _to_response(connector)


# =====================================================================
# Phase 8 Plan 08-02 Task 3 — IS_CONNECTED_TO Track bindings (B1 gap)
# =====================================================================


def _binding_row(connector_id: str, track: Any, edge: Any) -> ConnectorBindingResponse:
    """Project a (connector, track, edge) triple into the wire response shape.

    Per CLAUDE.md jvspatial pillar #2 (semantics on edges): the binding's
    relationship metadata (``mapping_profile_yaml`` + ``bidirectional``) is
    read from typed edge fields, NOT from an ``edge.context`` dict.
    """
    return ConnectorBindingResponse(
        connector_id=connector_id,
        track_id=track.id,
        track_title=getattr(track, "title", None),
        workspace_id=getattr(track, "workspace_id", None),
        mapping_profile_yaml=getattr(edge, "mapping_profile_yaml", "") or "",
        bidirectional=bool(getattr(edge, "bidirectional", False)),
    )


async def _list_bound_track_ids(connector: Any) -> List[str]:
    """Return the list of bound track ids — used for before/after snapshots."""
    try:
        tracks = await connector.nodes(
            edge=["IsConnectedTo"],
            direction="out",
            node=["Track"],
        )
        return [t.id for t in (tracks or [])]
    except Exception:
        return []


@endpoint(
    "/agentive/connectors/{connector_id}/bindings",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def post_create_binding(
    request: Request,
    connector_id: str,
) -> ConnectorBindingResponse:
    """Create an IS_CONNECTED_TO edge between this Connector and a Track.

    Body: ``CreateConnectorBindingRequest`` (Pydantic ``extra: forbid``).
    Gated via both ``connector.update`` AND ``track.update`` policy actions —
    binding writes data into the bound Track, so both ends must consent
    (T-08-02-E02 mitigation).

    Idempotent: a second POST with the same (connector, track) tuple returns
    the existing edge metadata without creating a duplicate (T-08-02-T03).

    Emits a single ``connector.update`` ChangeEvent (REUSES existing Literal —
    D-05 single emission preserved; no new ChangeEventAction member).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    try:
        raw_body = await request.json()
    except Exception:  # noqa: BLE001
        raw_body = {}
    if not isinstance(raw_body, dict):
        raise BadRequestError(message="Request body must be a JSON object")
    try:
        body = CreateConnectorBindingRequest.model_validate(raw_body)
    except Exception as e:  # noqa: BLE001
        raise BadRequestError(
            message="Invalid bindings POST body",
            details={"validation": str(e)},
        )

    connector = await get_connector(connector_id)
    if connector is None or connector.owner != user_id:
        # T-08-02-I01 — same canonical envelope for unknown vs cross-owner.
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )

    track = await Track.get(body.track_id)
    if track is None:
        raise ResourceNotFoundError(
            message=f"Track {body.track_id!r} not found",
            details={"track_id": body.track_id},
        )

    # T-08-02-E02 — operator must own the Track they're binding to.
    from app.services.permissions import resolve_role

    track_role = await resolve_role(user_id, "track", body.track_id)
    if track_role != "owner":
        # Same 404 envelope — never leak existence to non-owners.
        raise ResourceNotFoundError(
            message=f"Track {body.track_id!r} not found",
            details={"track_id": body.track_id},
        )

    # Two policy_evaluate calls — both ends of the binding must consent.
    decision_connector = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="connector.update",
        resource=Resource(
            kind="connector",
            id=connector.id,
            scope=f"user:{connector.owner or user_id}",
        ),
    )
    if not decision_connector.allowed:
        raise InsufficientPermissionsError(
            message="connector.update denied",
            details={"decision_reason": decision_connector.reason},
        )
    decision_track = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(
            kind="track",
            id=track.id,
            scope=f"track:{track.id}",
        ),
    )
    if not decision_track.allowed:
        raise InsufficientPermissionsError(
            message="track.update denied",
            details={"decision_reason": decision_track.reason},
        )

    # Idempotency — return existing edge metadata if already bound (T-08-02-T03).
    ctx = await connector.get_context()
    existing_edges = await ctx.find_edges_between(
        connector.id, track.id, edge_class=IS_CONNECTED_TO
    )
    before_bindings = await _list_bound_track_ids(connector)
    if existing_edges:
        # Already bound — return the existing edge, do NOT duplicate or emit.
        return _binding_row(connector.id, track, existing_edges[0])

    # Create the binding edge — typed metadata travels on the edge (pillar #2).
    await connector.connect(
        track,
        edge=IS_CONNECTED_TO,
        mapping_profile_yaml=body.mapping_profile_yaml or "",
        bidirectional=bool(body.bidirectional),
    )

    # Re-query to fetch the freshly-created edge.
    created_edges = await ctx.find_edges_between(
        connector.id, track.id, edge_class=IS_CONNECTED_TO
    )
    if not created_edges:
        # Defensive — connect succeeded but no edge surfaced.
        raise BadRequestError(
            message="binding created but not visible via find_edges_between",
            details={"connector_id": connector.id, "track_id": track.id},
        )
    new_edge = created_edges[0]
    after_bindings = await _list_bound_track_ids(connector)

    # D-05 single emission — REUSES connector.update Literal (no new member).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.update",
        resource_type="Connector",
        resource_id=connector.id,
        before={**_audit_snapshot(connector), "bindings": before_bindings},
        after={**_audit_snapshot(connector), "bindings": after_bindings},
        scope=f"user:{user_id}",
    )

    return _binding_row(connector.id, track, new_edge)


@endpoint(
    "/agentive/connectors/{connector_id}/bindings",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def list_bindings(
    request: Request,
    connector_id: str,
) -> ConnectorBindingListResponse:
    """List IS_CONNECTED_TO bindings for this Connector."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    connector = await get_connector(connector_id)
    if connector is None or connector.owner != user_id:
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="connector.read",
        resource=Resource(
            kind="connector",
            id=connector.id,
            scope=f"user:{connector.owner or user_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="connector.read denied",
            details={"decision_reason": decision.reason},
        )

    # Walk IS_CONNECTED_TO edges out to the bound Tracks (verified idiom
    # from sync_runtime.py:121-130).
    try:
        bound_tracks = await connector.nodes(
            edge=["IsConnectedTo"],
            direction="out",
            node=["Track"],
        )
    except Exception:
        bound_tracks = []

    rows: List[ConnectorBindingResponse] = []
    ctx = await connector.get_context()
    for track in bound_tracks or []:
        edges = await ctx.find_edges_between(
            connector.id, track.id, edge_class=IS_CONNECTED_TO
        )
        if not edges:
            continue
        rows.append(_binding_row(connector.id, track, edges[0]))

    return ConnectorBindingListResponse(bindings=rows, total=len(rows))


@endpoint(
    "/agentive/connectors/{connector_id}/bindings/{track_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Agentive"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_binding(
    request: Request,
    connector_id: str,
    track_id: str,
) -> None:
    """Remove the IS_CONNECTED_TO edge between this Connector and ``track_id``.

    204 on success; 404 on unknown connector OR cross-owner OR no binding.
    Emits a single ``connector.update`` ChangeEvent capturing the
    bindings-list delta.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    connector = await get_connector(connector_id)
    if connector is None or connector.owner != user_id:
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="connector.update",
        resource=Resource(
            kind="connector",
            id=connector.id,
            scope=f"user:{connector.owner or user_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="connector.update denied",
            details={"decision_reason": decision.reason},
        )

    track = await Track.get(track_id)
    if track is None:
        raise ResourceNotFoundError(
            message=f"Track {track_id!r} not found",
            details={"track_id": track_id},
        )

    ctx = await connector.get_context()
    edges = await ctx.find_edges_between(
        connector.id, track.id, edge_class=IS_CONNECTED_TO
    )
    if not edges:
        raise ResourceNotFoundError(
            message=(
                f"No IS_CONNECTED_TO binding between connector {connector_id!r} "
                f"and track {track_id!r}"
            ),
            details={"connector_id": connector_id, "track_id": track_id},
        )

    before_bindings = await _list_bound_track_ids(connector)
    for edge in edges:
        await edge.delete()
    after_bindings = await _list_bound_track_ids(connector)

    # D-05 single emission — REUSES connector.update Literal.
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.update",
        resource_type="Connector",
        resource_id=connector.id,
        before={**_audit_snapshot(connector), "bindings": before_bindings},
        after={**_audit_snapshot(connector), "bindings": after_bindings},
        scope=f"user:{user_id}",
    )

    return None


# ---------------------------------------------------------------------------
# Phase 18 QB-01 — QuickBooks OAuth2 endpoints
# ---------------------------------------------------------------------------


@endpoint(
    "/agentive/connectors/quickbooks/authorize",
    methods=["POST"],
    auth=True,
    tags=["Agentive", "QuickBooks"],
)
async def post_quickbooks_authorize(
    request: Request,
    connector_id: Optional[str] = None,
    redirect_uri: Optional[str] = None,
):
    """Build Intuit's consent URL + return it with a signed CSRF state token.

    The frontend opens ``consent_url`` in a popup; Intuit redirects back
    to ``redirect_uri`` (default: QUICKBOOKS_REDIRECT_URI env knob) with
    ``code``, ``state``, and ``realmId``. The callback endpoint validates
    ``state`` (verify_state) before exchanging the code.

    ``connector_id`` is optional — when present the callback will update
    that connector instead of creating a fresh one. The caller MUST own
    the connector (T-01-04-02 mitigation — return 404 on cross-user).
    """
    from app.schemas.agentive.quickbooks import QuickBooksAuthorizeResponse
    from app.services.connectors.quickbooks_oauth import build_consent_url

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    stored_client_id = None
    if connector_id:
        c = await get_connector(connector_id)
        if c is None or c.owner != user_id:
            raise ResourceNotFoundError(
                message=f"Connector {connector_id!r} not found",
                details={"connector_id": connector_id},
            )
        stored = dict(c.auth_state or {})
        stored_client_id = stored.get("client_id") or None

    consent_url, state = build_consent_url(
        user_id,
        redirect_uri=redirect_uri,
        client_id=stored_client_id,
        connector_id=connector_id,
    )
    return QuickBooksAuthorizeResponse(consent_url=consent_url, state=state)


@endpoint(
    "/agentive/connectors/quickbooks/callback",
    methods=["POST"],
    auth=True,
    tags=["Agentive", "QuickBooks"],
)
async def post_quickbooks_callback(
    request: Request,
    code: str,
    state: str,
    realmId: str,  # noqa: N803 — matches Intuit's redirect query key
    connector_id: Optional[str] = None,
    redirect_uri: Optional[str] = None,
):
    """Validate CSRF state, exchange the code, persist tokens + realm.

    On success returns the QuickBooksCallbackResponse with token material
    REDACTED — token values never appear in the response body. The
    connector's ``auth_state`` (server-side) holds the access + refresh
    tokens + expiries.

    On invalid_grant or any token-exchange failure: raises
    ConnectorAuthError(401). On state mismatch / expired: same.
    """
    from datetime import datetime, timedelta, timezone

    from app.api.errors import ConnectorAuthError
    from app.schemas.agentive.quickbooks import (
        QuickBooksCallbackResponse,
        quickbooks_safe_auth_state,
    )
    from app.services.connectors.quickbooks_oauth import (
        connector_id_from_state,
        exchange_code,
        verify_state,
    )

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    if not verify_state(state, user_id):
        raise ConnectorAuthError(
            message="QuickBooks OAuth callback rejected — state token invalid or expired",
            details={"reauth_required": False, "reason": "state_mismatch"},
        )

    resolved_id = connector_id or connector_id_from_state(state)
    existing_auth: Dict[str, Any] = {}
    existing_connector = None
    if resolved_id:
        existing_connector = await get_connector(resolved_id)
        if existing_connector is None or existing_connector.owner != user_id:
            raise ResourceNotFoundError(
                message=f"Connector {resolved_id!r} not found",
                details={"connector_id": resolved_id},
            )
        # Decrypt is a no-op for plaintext rows; MCP rows store the operator's
        # client_secret encrypted at rest (F-7).
        existing_auth = decrypt_auth_state(existing_connector.auth_state or {})

    token_response = await exchange_code(
        code,
        redirect_uri=redirect_uri,
        client_id=existing_auth.get("client_id") or None,
        client_secret=existing_auth.get("client_secret") or None,
    )
    now = datetime.now(timezone.utc)
    access_expires_at = now + timedelta(
        seconds=int(token_response.get("expires_in") or 3600)
    )
    rt_expires_in = int(token_response.get("x_refresh_token_expires_in") or 0)
    refresh_expires_at = (
        (now + timedelta(seconds=rt_expires_in)).isoformat() if rt_expires_in else ""
    )

    from app.config import settings

    environment = str(
        existing_auth.get("environment") or settings.QUICKBOOKS_ENVIRONMENT or "sandbox"
    )

    if (
        existing_connector is not None
        and existing_auth.get("catalog_slug") == "quickbooks_mcp"
    ):
        existing_connector.auth_state = encrypt_auth_state(
            {
                **existing_auth,
                "environment": environment,
            }
        )
        try:
            connector = await _finish_quickbooks_mcp_oauth(
                existing_connector,
                refresh_token=token_response.get("refresh_token") or "",
                realm_id=realmId,
            )
        except Exception as e:  # noqa: BLE001
            from app.utils.time import utc_now_iso

            existing_connector.health_status = "error"
            detail = describe_mcp_failure(e)
            existing_connector.last_error = detail
            existing_connector.updated_at = utc_now_iso()
            await existing_connector.save()
            raise BadRequestError(
                message=f"QuickBooks MCP mount failed: {detail}",
                details={"reason": detail},
            )
        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="connector.update",
            resource_type="Connector",
            resource_id=connector.id,
            before=None,
            after={**_audit_snapshot(connector), "realm_id": realmId},
            scope=f"user:{user_id}",
        )
        from app.schemas.agentive.connectors import mcp_safe_auth_state

        return QuickBooksCallbackResponse(
            connector_id=connector.id,
            realm_id=realmId,
            environment=environment,
            connected=True,
            reauth_required=False,
            auth_state=mcp_safe_auth_state(
                dict(getattr(connector, "auth_state", None) or {})
            ),
        )

    new_auth_state: Dict[str, Any] = {
        **existing_auth,
        "realm_id": realmId,
        "access_token": token_response.get("access_token") or "",
        "refresh_token": token_response.get("refresh_token") or "",
        "access_token_expires_at": access_expires_at.isoformat(),
        "refresh_token_expires_at": refresh_expires_at,
        "environment": environment,
    }

    # Update existing connector OR create a fresh one.
    if existing_connector is not None:
        existing_connector.auth_state = new_auth_state
        existing_connector.subclass_slug = "quickbooks"
        await existing_connector.save()
        connector = existing_connector
        action = "connector.update"
    else:
        connector = await create_connector(
            owner=user_id,
            auth_state=new_auth_state,
        )
        connector.subclass_slug = "quickbooks"
        await connector.save()
        action = "connector.create"

    # D-05 single emission — token material excluded via _audit_snapshot.
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action=cast(ChangeEventAction, action),
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after={**_audit_snapshot(connector), "realm_id": realmId},
        scope=f"user:{user_id}",
    )

    return QuickBooksCallbackResponse(
        connector_id=connector.id,
        realm_id=realmId,
        environment=environment,
        connected=True,
        reauth_required=False,
        auth_state=quickbooks_safe_auth_state(new_auth_state),
    )


# ---------------------------------------------------------------------------
# Phase 19 EML-01 — Gmail OAuth + label endpoints
# ---------------------------------------------------------------------------


@endpoint(
    "/agentive/connectors/gmail/oauth/start",
    methods=["POST"],
    auth=True,
    tags=["Agentive", "Gmail"],
)
async def post_gmail_oauth_start(
    request: Request,
    connector_id: Optional[str] = None,
    redirect_uri: Optional[str] = None,
):
    """Build Google's consent URL + return it with a signed CSRF state token."""
    from app.schemas.agentive.gmail import GmailOAuthStartResponse
    from app.services.connectors.gmail_oauth import build_consent_url

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    stored_client_id = None
    if connector_id:
        c = await get_connector(connector_id)
        if c is None or c.owner != user_id:
            raise ResourceNotFoundError(
                message=f"Connector {connector_id!r} not found",
                details={"connector_id": connector_id},
            )
        stored_client_id = dict(c.auth_state or {}).get("client_id") or None
    url, state = build_consent_url(
        user_id,
        redirect_uri=redirect_uri,
        client_id=stored_client_id,
        connector_id=connector_id,
    )
    return GmailOAuthStartResponse(consent_url=url, state=state)


@endpoint(
    "/agentive/connectors/gmail/oauth/callback",
    methods=["POST"],
    auth=True,
    tags=["Agentive", "Gmail"],
)
async def post_gmail_oauth_callback(
    request: Request,
    code: str,
    state: str,
    connector_id: Optional[str] = None,
    redirect_uri: Optional[str] = None,
):
    """Validate state, exchange the code, persist tokens (server-side).

    Token material is REDACTED from the wire response.
    """
    from datetime import datetime, timedelta, timezone

    from app.api.errors import ConnectorAuthError
    from app.schemas.agentive.gmail import (
        GmailOAuthCallbackResponse,
        gmail_safe_auth_state,
    )
    from app.services.connectors.gmail_oauth import (
        connector_id_from_state,
        exchange_code,
        verify_state,
    )

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if not verify_state(state, user_id):
        raise ConnectorAuthError(
            message="Gmail OAuth callback rejected — state token invalid or expired",
            details={"reauth_required": False, "reason": "state_mismatch"},
        )
    resolved_id = connector_id or connector_id_from_state(state)
    existing_auth: Dict[str, Any] = {}
    existing_connector = None
    if resolved_id:
        existing_connector = await get_connector(resolved_id)
        if existing_connector is None or existing_connector.owner != user_id:
            raise ResourceNotFoundError(
                message=f"Connector {resolved_id!r} not found",
                details={"connector_id": resolved_id},
            )
        existing_auth = dict(existing_connector.auth_state or {})
    token_response = await exchange_code(
        code,
        redirect_uri=redirect_uri,
        client_id=existing_auth.get("client_id") or None,
        client_secret=existing_auth.get("client_secret") or None,
    )
    now = datetime.now(timezone.utc)
    access_expires_at = now + timedelta(
        seconds=int(token_response.get("expires_in") or 3600)
    )
    new_auth_state: Dict[str, Any] = {
        **existing_auth,
        "access_token": token_response.get("access_token") or "",
        "refresh_token": token_response.get("refresh_token") or "",
        "access_token_expires_at": access_expires_at.isoformat(),
        "labels": existing_auth.get("labels") or [],
        "consent_acknowledged": bool(existing_auth.get("consent_acknowledged")),
    }
    if existing_connector is not None:
        existing_connector.auth_state = new_auth_state
        existing_connector.subclass_slug = "gmail"
        await existing_connector.save()
        connector = existing_connector
        action = "connector.update"
    else:
        connector = await create_connector(
            owner=user_id,
            auth_state=new_auth_state,
        )
        connector.subclass_slug = "gmail"
        await connector.save()
        action = "connector.create"

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action=cast(ChangeEventAction, action),
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after=_audit_snapshot(connector),
        scope=f"user:{user_id}",
    )
    return GmailOAuthCallbackResponse(
        connector_id=connector.id,
        connected=True,
        reauth_required=False,
        auth_state=gmail_safe_auth_state(connector.auth_state or {}),
    )


@endpoint(
    "/agentive/connectors/{connector_id}/gmail/labels",
    methods=["GET"],
    auth=True,
    tags=["Agentive", "Gmail"],
)
async def get_gmail_labels(request: Request, connector_id: str):
    """List the connected Gmail account's labels."""
    import httpx

    from app.api.errors import ConnectorAuthError
    from app.schemas.agentive.gmail import GmailLabel, GmailLabelsResponse

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    c = await get_connector(connector_id)
    if c is None or c.owner != user_id:
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )
    access_token = (c.auth_state or {}).get("access_token") or ""
    if not access_token:
        raise ConnectorAuthError(
            message="Gmail connector has no access_token — complete OAuth first",
            details={"reauth_required": True},
        )
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/labels",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise ConnectorAuthError(
            message=f"Gmail labels endpoint returned {resp.status_code}",
            details={
                "status_code": resp.status_code,
                "reauth_required": resp.status_code == 401,
            },
        )
    body = resp.json() or {}
    out_labels = [
        GmailLabel(
            id=str(lbl.get("id") or ""),
            name=str(lbl.get("name") or ""),
            type=str(lbl.get("type") or "user"),
        )
        for lbl in body.get("labels") or []
    ]
    return GmailLabelsResponse(labels=out_labels)


@endpoint(
    "/agentive/connectors/{connector_id}/gmail/labels",
    methods=["POST"],
    auth=True,
    tags=["Agentive", "Gmail"],
)
async def post_gmail_set_labels(
    request: Request,
    connector_id: str,
    label_ids: List[str],
    consent_acknowledged: bool = False,
):
    """Persist the operator's selected Gmail labels + consent acknowledgement.

    The Gmail connector is INACTIVE for sync until ``consent_acknowledged``
    is True. ``label_ids`` is stored verbatim on ``auth_state.labels`` and
    drives every subsequent ``sync_pull`` (I-CON-EML-01).
    """
    from datetime import datetime, timezone

    from app.schemas.agentive.gmail import (
        GmailSetLabelsResponse,
        gmail_safe_auth_state,
    )

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    c = await get_connector(connector_id)
    if c is None or c.owner != user_id:
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )
    if not isinstance(label_ids, list) or not all(
        isinstance(x, str) for x in label_ids
    ):
        raise BadRequestError(message="label_ids must be a list of strings")
    new_state = dict(c.auth_state or {})
    new_state["labels"] = list(label_ids)
    consent_marker = {
        "acknowledged_by_user_id": user_id,
        "acknowledged_at": datetime.now(timezone.utc).isoformat(),
        "label_ids": list(label_ids),
    }
    new_state["consent_acknowledged"] = bool(consent_acknowledged)
    if consent_acknowledged:
        new_state["consent"] = consent_marker
    c.auth_state = new_state
    await c.save()
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.update",
        resource_type="Connector",
        resource_id=c.id,
        before=None,
        after={**_audit_snapshot(c), "labels": list(label_ids)},
        scope=f"user:{user_id}",
    )
    return GmailSetLabelsResponse(
        connector_id=c.id,
        label_ids=list(label_ids),
        consent_acknowledged=bool(consent_acknowledged),
        auth_state=gmail_safe_auth_state(c.auth_state or {}),
    )
