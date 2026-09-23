"""MCP connector mount / unmount / rehydrate (ADR-009 registration class).

Hot-registers discovered remote tools into the per-workspace tool registry
via :func:`register_workspace_tools` — the same registration class
``finalize_install`` uses for App bundle tools. No process restart required.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agentive.connectors.mcp_client import (
    HEALTH_ERROR,
    HEALTH_OK,
    RemoteTool,
    list_remote_tools,
    probe_mcp_health,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

MCP_SUBCLASS_SLUG = "mcp"
MCP_BUNDLE_PREFIX = "mcp:"
_MCP_CANONICAL_BUNDLE_PREFIX = "mcp:canonical:"
_HANDLER_REF = "app.agentive.connectors.mcp_proxy:invoke"
_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_]+")

#: Live row refs per (workspace, catalog slug) backing the canonical keys.
#: The canonical set survives until the LAST row of the slug unregisters.
#: In-process like the workspace registry itself; rebuilt by rehydrate.
_CANONICAL_MCP_ROWS: Dict[Tuple[str, str], Set[str]] = {}


def mcp_bundle_slug(connector_id: str) -> str:
    """Synthetic bundle slug for workspace tool registry (unregister key)."""
    return f"{MCP_BUNDLE_PREFIX}{connector_id}"


def tool_key_for(connector_id: str, remote_name: str) -> str:
    """Collision-safe workspace tool key: ``mcp__{short}__{remote}``."""
    short = _SAFE_NAME.sub("_", connector_id.split(".")[-1][:12]).strip("_") or "c"
    remote = _SAFE_NAME.sub("_", remote_name).strip("_") or "tool"
    return f"mcp__{short}__{remote}"


def _auth_state_from_mount(
    *,
    transport: str,
    command: Optional[str],
    args: Optional[List[str]],
    env: Optional[Dict[str, str]],
    url: Optional[str],
    headers: Optional[Dict[str, str]],
    display_name: Optional[str],
    discovered: Optional[List[Dict[str, Any]]] = None,
    registry_name: Optional[str] = None,
    registry_version: Optional[str] = None,
    catalog_slug: Optional[str] = None,
) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "transport": transport,
        "display_name": display_name or "",
    }
    if catalog_slug:
        state["catalog_slug"] = catalog_slug
    if registry_name:
        state["registry"] = {
            "name": registry_name,
            "version": registry_version or "",
        }
    if transport == "stdio":
        state["command"] = command or ""
        state["args"] = list(args or [])
        if env:
            state["env"] = dict(env)
    else:
        state["url"] = url or ""
        if headers:
            state["headers"] = dict(headers)
    if discovered is not None:
        state["discovered_tools"] = discovered
    return state


def _specs_from_tools(
    connector_id: str, tools: List[RemoteTool]
) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    for t in tools:
        specs.append(
            {
                "key": tool_key_for(connector_id, t.name),
                "handler_ref": _HANDLER_REF,
                "input_schema": t.input_schema or {"type": "object", "properties": {}},
                "description": t.description or t.name,
                "privileged": False,
                "_mcp_connector_id": connector_id,
                "_mcp_remote_name": t.name,
                # Display only — see connectors/mcp_tool_class.py.
                "_mcp_annotations": dict(getattr(t, "annotations", {}) or {}),
            }
        )
    return specs


def register_mcp_tools(
    workspace_id: str,
    connector_id: str,
    tools: List[RemoteTool],
    *,
    catalog_slug: str = "",
) -> None:
    """Hot-register remote tools into the workspace registry (no restart).

    Registers the per-row keys plus, when ``catalog_slug`` is known, the
    canonical slug keys (shared across rows — idempotent upsert).
    """
    from app.services.hooks.registry import (
        register_workspace_tools,
        unregister_bundle_registrations,
    )

    slug = mcp_bundle_slug(connector_id)
    unregister_bundle_registrations(workspace_id, slug)
    specs = _specs_from_tools(connector_id, tools)
    register_workspace_tools(workspace_id, slug, specs)
    logger.info(
        "mcp_mount: registered %d tools for connector %s in workspace %s",
        len(specs),
        connector_id,
        workspace_id,
    )
    if catalog_slug:
        register_canonical_mcp_tools(workspace_id, connector_id, catalog_slug, tools)


def unregister_mcp_tools(
    workspace_id: str, connector_id: str, catalog_slug: str = ""
) -> None:
    """Drop MCP tool registrations for one connector.

    Canonical slug keys survive while any other row of the slug is still
    registered.
    """
    from app.services.hooks.registry import unregister_bundle_registrations

    unregister_bundle_registrations(workspace_id, mcp_bundle_slug(connector_id))
    if not catalog_slug:
        return
    key = (workspace_id, catalog_slug)
    rows = _CANONICAL_MCP_ROWS.get(key)
    if rows is not None:
        rows.discard(connector_id)
        if rows:
            return
        _CANONICAL_MCP_ROWS.pop(key, None)
    unregister_bundle_registrations(
        workspace_id, canonical_mcp_bundle_slug(catalog_slug)
    )


def reset_canonical_mcp_rows_for_tests() -> None:
    """Test helper — drop the in-process canonical refcounts."""
    _CANONICAL_MCP_ROWS.clear()


def track_canonical_mcp_row(workspace_id: str, slug: str, connector_id: str) -> None:
    """Record one live row backing a canonical slug set (refcount)."""
    _CANONICAL_MCP_ROWS.setdefault((workspace_id, slug), set()).add(connector_id)


def canonical_mcp_bundle_slug(slug: str) -> str:
    """Canonical bundle slug for one MCP capability (unregister key)."""
    return f"{_MCP_CANONICAL_BUNDLE_PREFIX}{slug}"


def canonical_mcp_tool_key(slug: str, remote_name: str) -> str:
    """Stable workspace tool key: ``mcp__{slug}__{remote}`` (no row id)."""
    safe_slug = _SAFE_NAME.sub("_", slug).strip("_") or "server"
    safe_remote = _SAFE_NAME.sub("_", remote_name).strip("_") or "tool"
    return f"mcp__{safe_slug}__{safe_remote}"


def catalog_slug_for_connector(connector: Any) -> str:
    """Catalog slug for an MCP row (decrypted auth_state; "" when unknown)."""
    try:
        from app.agentive.services.connector_registry_node import (
            decrypt_auth_state,
        )

        auth = decrypt_auth_state(getattr(connector, "auth_state", None) or {})
        return str(auth.get("catalog_slug") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _canonical_mcp_spec(
    slug: str,
    name: str,
    description: str,
    input_schema: Dict[str, Any],
    annotations: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "key": canonical_mcp_tool_key(slug, name),
        "handler_ref": _HANDLER_REF,
        "input_schema": dict(input_schema or {"type": "object", "properties": {}}),
        "description": description or name,
        "privileged": False,
        "_mcp_connector_slug": slug,
        "_mcp_remote_name": name,
        # Display only — see connectors/mcp_tool_class.py.
        "_mcp_annotations": dict(annotations or {}),
    }


def canonical_mcp_specs_from_tools(
    slug: str, tools: List[RemoteTool]
) -> List[Dict[str, Any]]:
    """Row-agnostic specs from live RemoteTool objects (mount/refresh path)."""
    return [
        _canonical_mcp_spec(
            slug,
            t.name,
            t.description,
            t.input_schema,
            dict(getattr(t, "annotations", {}) or {}),
        )
        for t in tools or []
        if (getattr(t, "name", "") or "").strip()
    ]


def canonical_mcp_specs_from_discovered(
    slug: str, discovered: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Row-agnostic specs from persisted discovered dicts (rehydrate path)."""
    specs: List[Dict[str, Any]] = []
    for item in discovered or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        schema = item.get("input_schema")
        specs.append(
            _canonical_mcp_spec(
                slug,
                name,
                str(item.get("description") or ""),
                schema if isinstance(schema, dict) else {},
                (
                    item.get("annotations")
                    if isinstance(item.get("annotations"), dict)
                    else {}
                ),
            )
        )
    return specs


def register_canonical_mcp_tools(
    workspace_id: str,
    connector_id: str,
    slug: str,
    tools: List[RemoteTool],
) -> int:
    """Register (idempotent upsert) the canonical slug keys for one row."""
    from app.services.hooks.registry import register_workspace_tools

    specs = canonical_mcp_specs_from_tools(slug, tools)
    register_workspace_tools(workspace_id, canonical_mcp_bundle_slug(slug), specs)
    track_canonical_mcp_row(workspace_id, slug, connector_id)
    return len(specs)


async def materialize_mcp_policies(*, connector: Any, actor_id: str) -> Any:
    """I-CON-04 for MCP: grant connector subject tool.invoke + connector.read.

    Idempotent. Google Drive / HTTP MCP mounts create the Connector with
    ``skip_default_policy=True`` and historically never called this, so
    invoke fail-closed with ``fail_closed_no_policy`` after the user approved.
    Re-running is a no-op when the grant already exists; if a Policy row
    exists without ``HAS_POLICY``, the edge is wired instead of duplicating.
    """
    from app.middleware.permissions_cache import policy_decision_clear_for_subject
    from app.models.nodes import Policy
    from app.services.policy_registry import (
        attach_policy_edge,
        create_policy,
        list_policies_for_subject,
    )

    def _grants_invoke(policy: Any) -> bool:
        if not getattr(policy, "is_active", True):
            return False
        return "tool.invoke" in (getattr(policy, "actions", None) or [])

    attached = await list_policies_for_subject("connector", connector.id)
    for policy in attached:
        if _grants_invoke(policy):
            return policy

    orphans = list(
        await Policy.find(
            {
                "context.subject_kind": "connector",
                "context.subject_id": connector.id,
            }
        )
    )
    for policy in orphans:
        if not _grants_invoke(policy):
            continue
        try:
            await attach_policy_edge(
                policy=policy, subject_kind="connector", subject_id=connector.id
            )
        except Exception:  # noqa: BLE001 — evaluate may still find it next
            logger.warning(
                "mcp_mount: HAS_POLICY rewire failed for connector=%s policy=%s",
                connector.id,
                getattr(policy, "id", None),
                exc_info=True,
            )
        policy_decision_clear_for_subject("connector", connector.id)
        return policy

    created = await create_policy(
        subject_kind="connector",
        subject_id=connector.id,
        scope=f"connector:{connector.id}",
        actions=["tool.invoke", "connector.read"],
        entry_types=[],
        tags=[],
        requires_human_approval=False,
        is_active=True,
        created_by=actor_id,
    )
    policy_decision_clear_for_subject("connector", connector.id)
    return created


async def mount_mcp_connector(
    *,
    owner_id: str,
    workspace_id: str,
    transport: str,
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    url: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    display_name: Optional[str] = None,
    registry_name: Optional[str] = None,
    registry_version: Optional[str] = None,
    catalog_slug: Optional[str] = None,
) -> Any:
    """Create Connector, discover tools, hot-register, materialize policies.

    Returns the persisted Connector node. Raises on discovery failure after
    best-effort cleanup of a half-created connector is *not* attempted —
    callers may DELETE; health fields capture the error when probe fails
    post-create. Discovery failure before create raises without a Node.
    """
    from app.agentive.services.connector_registry_node import create_connector

    transport = (transport or "stdio").strip().lower()
    if transport not in ("stdio", "streamable_http"):
        raise ValueError(f"unsupported transport: {transport!r}")
    if transport == "stdio" and not (command or "").strip():
        raise ValueError("stdio mount requires command")
    if transport == "streamable_http" and not (url or "").strip():
        raise ValueError("streamable_http mount requires url")
    if transport == "streamable_http":
        # Port allowlist + public-address check. The per-redirect-hop
        # revalidation and DNS pinning happen in the guarded client used at
        # session time (see url_safety.guarded_async_client).
        from app.services.url_safety import validate_outbound_http_url

        await validate_outbound_http_url((url or "").strip())

    provisional = _auth_state_from_mount(
        transport=transport,
        command=command,
        args=args,
        env=env,
        url=url,
        headers=headers,
        display_name=display_name,
        registry_name=registry_name,
        registry_version=registry_version,
        catalog_slug=catalog_slug,
    )
    # Discover BEFORE create so a bad endpoint does not leave a dead Node.
    tools = await list_remote_tools(provisional)
    discovered = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]
    auth_state = _auth_state_from_mount(
        transport=transport,
        command=command,
        args=args,
        env=env,
        url=url,
        headers=headers,
        display_name=display_name,
        discovered=discovered,
        registry_name=registry_name,
        registry_version=registry_version,
        catalog_slug=catalog_slug,
    )

    connector = await create_connector(
        owner=owner_id,
        kind="mcp",
        auth_state=auth_state,
        capabilities=["mcp.client"],
        # Skip default sync policy — MCP path materializes its own below.
        skip_default_policy=True,
    )
    connector.subclass_slug = MCP_SUBCLASS_SLUG
    connector.workspace_id = workspace_id
    connector.health_status = HEALTH_OK
    connector.last_error = None
    connector.last_health_at = utc_now_iso()
    connector.updated_at = utc_now_iso()
    await connector.save()

    await materialize_mcp_policies(connector=connector, actor_id=owner_id)
    register_mcp_tools(
        workspace_id,
        connector.id,
        tools,
        catalog_slug=catalog_slug_for_connector(connector),
    )

    try:
        from app.agentive.workspace_agent_profile import invalidate_workspace_profile

        invalidate_workspace_profile(workspace_id)
    except Exception:  # noqa: BLE001
        logger.exception("mcp_mount: profile invalidate failed")

    return connector


async def complete_stdio_mount(
    connector: Any,
    *,
    command: str,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    display_name: Optional[str] = None,
    catalog_slug: Optional[str] = None,
) -> Any:
    """Discover tools on an existing Connector and register them (stdio).

    Used when OAuth must finish before spawn (QuickBooks MCP). Does not
    create a second Node.
    """
    if not (command or "").strip():
        raise ValueError("stdio mount requires command")
    workspace_id = getattr(connector, "workspace_id", "") or ""
    if not workspace_id:
        raise ValueError("MCP connector missing workspace_id")
    prior = dict(getattr(connector, "auth_state", None) or {})
    slug = catalog_slug or prior.get("catalog_slug")
    name = display_name or prior.get("display_name")
    provisional = _auth_state_from_mount(
        transport="stdio",
        command=command,
        args=args,
        env=env,
        url=None,
        headers=None,
        display_name=name,
        catalog_slug=slug,
    )
    tools = await list_remote_tools(provisional)
    discovered = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]
    auth_state = _auth_state_from_mount(
        transport="stdio",
        command=command,
        args=args,
        env=env,
        url=None,
        headers=None,
        display_name=name,
        discovered=discovered,
        catalog_slug=slug,
    )
    # Encrypt secrets at rest — this state carries the spawn env (OAuth tokens,
    # client secrets). Assigning the plaintext dict here left a window where
    # they were persisted in the clear.
    from app.agentive.services.connector_registry_node import encrypt_auth_state

    connector.auth_state = encrypt_auth_state(auth_state)
    connector.kind = "mcp"
    connector.subclass_slug = MCP_SUBCLASS_SLUG
    connector.health_status = HEALTH_OK
    connector.last_error = None
    connector.last_health_at = utc_now_iso()
    connector.updated_at = utc_now_iso()
    await connector.save()

    owner_id = getattr(connector, "owner", "") or ""
    await materialize_mcp_policies(connector=connector, actor_id=owner_id)
    register_mcp_tools(workspace_id, connector.id, tools, catalog_slug=slug or "")

    try:
        from app.agentive.workspace_agent_profile import invalidate_workspace_profile

        invalidate_workspace_profile(workspace_id)
    except Exception:  # noqa: BLE001
        logger.exception("mcp_mount: profile invalidate failed on complete")

    return connector


async def refresh_mcp_connector(connector: Any) -> Any:
    """Re-discover tools and re-register (no restart). Updates health fields."""
    workspace_id = getattr(connector, "workspace_id", "") or ""
    if not workspace_id:
        raise ValueError("MCP connector missing workspace_id")
    auth_state = dict(getattr(connector, "auth_state", None) or {})
    try:
        from app.agentive.connectors.mcp_adapter import _refresh_oauth_if_needed

        await _refresh_oauth_if_needed(connector)
        auth_state = dict(getattr(connector, "auth_state", None) or {})
        tools = await list_remote_tools(auth_state)
        discovered = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]
        auth_state["discovered_tools"] = discovered
        # Re-encrypt: `auth_state` was decrypted for the remote call above.
        from app.agentive.services.connector_registry_node import encrypt_auth_state

        connector.auth_state = encrypt_auth_state(auth_state)
        connector.health_status = HEALTH_OK
        connector.last_error = None
        connector.last_health_at = utc_now_iso()
        connector.updated_at = utc_now_iso()
        await connector.save()
        register_mcp_tools(
            workspace_id,
            connector.id,
            tools,
            catalog_slug=catalog_slug_for_connector(connector),
        )
        try:
            owner_id = getattr(connector, "owner", "") or ""
            await materialize_mcp_policies(connector=connector, actor_id=owner_id)
        except Exception:  # noqa: BLE001 — refresh still returns the connector
            logger.exception(
                "mcp_mount: MCP policy heal failed on refresh for %s", connector.id
            )
    except Exception as exc:  # noqa: BLE001
        connector.health_status = HEALTH_ERROR
        connector.last_error = str(exc)[:500]
        connector.last_health_at = utc_now_iso()
        connector.updated_at = utc_now_iso()
        await connector.save()
        raise
    try:
        from app.agentive.workspace_agent_profile import invalidate_workspace_profile

        invalidate_workspace_profile(workspace_id)
    except Exception:  # noqa: BLE001
        logger.exception("mcp_mount: profile invalidate failed on refresh")
    return connector


async def health_check_mcp_connector(connector: Any) -> Dict[str, Any]:
    """Probe remote MCP and persist health fields on the Connector."""
    from app.agentive.connectors.mcp_oauth import OAUTH_STATUS_PENDING

    auth_state = dict(getattr(connector, "auth_state", None) or {})
    oauth = auth_state.get("oauth") if isinstance(auth_state.get("oauth"), dict) else {}
    if oauth.get("status") == OAUTH_STATUS_PENDING:
        result = {
            "status": "unknown",
            "error": "Finish sign-in to authorize this MCP server",
            "tool_count": 0,
        }
    else:
        try:
            from app.agentive.connectors.mcp_adapter import _refresh_oauth_if_needed

            await _refresh_oauth_if_needed(connector)
            auth_state = dict(getattr(connector, "auth_state", None) or {})
        except Exception:  # noqa: BLE001
            logger.warning("mcp_mount: oauth refresh before health probe failed")
        result = await probe_mcp_health(auth_state)
    connector.health_status = result["status"]
    connector.last_error = result.get("error")
    connector.last_health_at = utc_now_iso()
    connector.updated_at = connector.last_health_at
    await connector.save()
    return {
        "connector_id": connector.id,
        "status": connector.health_status,
        "last_error": connector.last_error,
        "last_health_at": connector.last_health_at,
        "tool_count": result.get("tool_count", 0),
    }


async def unmount_mcp_connector(connector: Any) -> bool:
    """Unregister workspace tools then delete the Connector Node."""
    from app.agentive.services.connector_registry_node import delete_connector

    workspace_id = getattr(connector, "workspace_id", "") or ""
    if workspace_id:
        unregister_mcp_tools(
            workspace_id, connector.id, catalog_slug_for_connector(connector)
        )
        try:
            from app.agentive.workspace_agent_profile import (
                invalidate_workspace_profile,
            )

            invalidate_workspace_profile(workspace_id)
        except Exception:  # noqa: BLE001
            logger.exception("mcp_mount: profile invalidate failed on unmount")
    # Remove the on-disk stdio token store (plaintext OAuth material). The
    # DELETE route purges too, but direct callers of this function would
    # otherwise leave the file behind for the life of the box.
    try:
        from app.agentive.services.connector_registry_node import decrypt_auth_state
        from app.connectors.stdio_env import purge_token_store_for_auth_state

        purge_token_store_for_auth_state(
            decrypt_auth_state(getattr(connector, "auth_state", None) or {})
        )
    except Exception:  # noqa: BLE001 — teardown must not block the delete
        logger.exception("mcp_mount: token-store purge failed on unmount")
    return await delete_connector(connector.id)


async def rehydrate_mcp_connectors() -> None:
    """Re-register MCP tools for every mcp Connector after process restart.

    Mirrors :func:`rehydrate_all_installed_bundles`. Best-effort per connector.
    """
    from app.agentive.nodes import Connector

    try:
        connectors = await Connector.find({"subclass_slug": MCP_SUBCLASS_SLUG})
    except Exception as exc:  # noqa: BLE001
        logger.warning("rehydrate_mcp_connectors: find failed: %s", exc)
        return

    ok = 0
    err = 0
    for c in connectors or []:
        workspace_id = getattr(c, "workspace_id", "") or ""
        if not workspace_id:
            continue
        auth_state = dict(getattr(c, "auth_state", None) or {})
        cached = auth_state.get("discovered_tools") or []
        try:
            if cached:
                tools = [
                    RemoteTool(
                        name=str(item.get("name") or ""),
                        description=str(item.get("description") or ""),
                        input_schema=dict(item.get("input_schema") or {}),
                    )
                    for item in cached
                    if item.get("name")
                ]
            else:
                tools = await list_remote_tools(auth_state)
            register_mcp_tools(
                workspace_id, c.id, tools, catalog_slug=catalog_slug_for_connector(c)
            )
            try:
                await materialize_mcp_policies(
                    connector=c, actor_id=getattr(c, "owner", "") or ""
                )
            except Exception:  # noqa: BLE001 — tools are registered; grant is extra
                logger.warning(
                    "rehydrate_mcp_connectors: policy heal failed for %s",
                    c.id,
                    exc_info=True,
                )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            err += 1
            logger.warning("rehydrate_mcp_connectors: failed for %s: %s", c.id, exc)
            try:
                c.health_status = HEALTH_ERROR
                c.last_error = str(exc)[:500]
                c.last_health_at = utc_now_iso()
                await c.save()
            except Exception:  # noqa: BLE001
                pass
    logger.info(
        "rehydrate_mcp_connectors: ok=%d err=%d",
        ok,
        err,
    )
