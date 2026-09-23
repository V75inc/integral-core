"""Native connector tool registration (Drive / Sheets / Gmail).

Hot-registers the connector's ``TOOL_SPECS`` into the per-workspace tool
registry via :func:`register_workspace_tools` — the same registration
class ``mcp_mount`` uses for hosted MCP tools and ``finalize_install``
uses for App bundle tools. No process restart required.

Two key families per capability slug:

- Per-row keys ``native__{short}__{tool}`` (parallel to
  ``mcp__{short}__{remote}``) — kept for backward compatibility
  (in-flight staged cards, autonomy grants, existing mounts).
- Canonical keys ``{slug}__{tool}`` (e.g. ``drive_native__search_files``)
  — slug-addressed, row-agnostic. The proxy resolves the caller's row
  per call (personal wins, else shared), so one key serves every member
  correctly. This is the advertised surface; per-row keys stay
  dispatchable but are omitted from the announcement doc.

Specs carry ``_native_connector_id`` / ``_native_tool_name`` (row keys)
or ``_native_connector_slug`` / ``_native_tool_name`` (canonical),
routed by ``run_tool`` to ``native_google_proxy.invoke_from_spec``,
plus ``_native_write`` — the vetted write classification the dispatch
bless gate consults (no remote hint to distrust: first-party code).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

NATIVE_BUNDLE_PREFIX = "native:"
_HANDLER_REF = "app.agentive.connectors.native_google_proxy:invoke"
_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_]+")

#: Connector subclass slugs served by this registry.
NATIVE_GOOGLE_SLUGS = ("drive_native", "sheets_native", "gmail")

#: Canonical bundle slug per capability: ``native:canonical:<slug>``.
_CANONICAL_BUNDLE_PREFIX = "native:canonical:"

#: Live row refs per (workspace, slug) backing the canonical keys. The
#: canonical set survives until the LAST row of the slug unregisters —
#: uninstalling your personal row must not drop the shared row's tools.
#: In-process like the workspace registry itself; rebuilt by rehydrate.
_CANONICAL_ROWS: Dict[Tuple[str, str], Set[str]] = {}


def native_bundle_slug(connector_id: str) -> str:
    """Synthetic bundle slug for workspace tool registry (unregister key)."""
    return f"{NATIVE_BUNDLE_PREFIX}{connector_id}"


def canonical_bundle_slug(slug: str) -> str:
    """Canonical bundle slug for one capability (unregister key)."""
    return f"{_CANONICAL_BUNDLE_PREFIX}{slug}"


def canonical_tool_key(slug: str, tool_name: str) -> str:
    """Stable workspace tool key: ``{slug}__{tool}`` (no row id)."""
    safe_slug = _SAFE_NAME.sub("_", slug).strip("_") or "tool"
    safe_tool = _SAFE_NAME.sub("_", tool_name).strip("_") or "tool"
    return f"{safe_slug}__{safe_tool}"


def tool_key_for(connector_id: str, tool_name: str) -> str:
    """Collision-safe workspace tool key: ``native__{short}__{tool}``."""
    short = _SAFE_NAME.sub("_", connector_id.split(".")[-1][:12]).strip("_") or "c"
    remote = _SAFE_NAME.sub("_", tool_name).strip("_") or "tool"
    return f"native__{short}__{remote}"


def _tool_defs(slug: str) -> List[Dict[str, Any]]:
    if slug == "drive_native":
        from app.agentive.connectors.drive_native import TOOL_SPECS
    elif slug == "sheets_native":
        from app.agentive.connectors.sheets_native import TOOL_SPECS
    elif slug == "gmail":
        from app.agentive.connectors.gmail import TOOL_SPECS
    else:
        raise ValueError(f"unknown native Google slug {slug!r}")
    return TOOL_SPECS


def specs_for_slug(connector_id: str, slug: str) -> List[Dict[str, Any]]:
    """Build workspace tool specs for one native connector (no registry I/O)."""
    specs: List[Dict[str, Any]] = []
    for t in _tool_defs(slug):
        specs.append(
            {
                "key": tool_key_for(connector_id, t["name"]),
                "handler_ref": _HANDLER_REF,
                "input_schema": dict(t.get("input_schema") or {}),
                "description": str(t.get("description") or t["name"]),
                "privileged": False,
                "_native_connector_id": connector_id,
                "_native_tool_name": t["name"],
                # Vetted write classification (first-party specs — the ONLY
                # source the bless gate consults for native tools).
                "_native_write": bool(t.get("write", False)),
            }
        )
    return specs


def canonical_specs_for_slug(slug: str) -> List[Dict[str, Any]]:
    """Build row-agnostic specs for one capability (no registry I/O).

    The proxy resolves the caller's row per call, so these carry the slug
    instead of a row id.
    """
    specs: List[Dict[str, Any]] = []
    for t in _tool_defs(slug):
        specs.append(
            {
                "key": canonical_tool_key(slug, t["name"]),
                "handler_ref": _HANDLER_REF,
                "input_schema": dict(t.get("input_schema") or {}),
                "description": str(t.get("description") or t["name"]),
                "privileged": False,
                "_native_connector_slug": slug,
                "_native_tool_name": t["name"],
                "_native_write": bool(t.get("write", False)),
            }
        )
    return specs


def register_native_tools(workspace_id: str, connector_id: str, slug: str) -> int:
    """Hot-register native tools into the workspace registry (no restart).

    Registers both the per-row keys and the canonical slug keys (shared
    across rows — idempotent upsert).
    """
    from app.services.hooks.registry import (
        register_workspace_tools,
        unregister_bundle_registrations,
    )

    bundle = native_bundle_slug(connector_id)
    unregister_bundle_registrations(workspace_id, bundle)
    specs = specs_for_slug(connector_id, slug)
    register_workspace_tools(workspace_id, bundle, specs)
    canonical = canonical_specs_for_slug(slug)
    register_workspace_tools(workspace_id, canonical_bundle_slug(slug), canonical)
    _CANONICAL_ROWS.setdefault((workspace_id, slug), set()).add(connector_id)
    logger.info(
        "native_tool_registry: registered %d tools for connector %s in workspace %s",
        len(specs),
        connector_id,
        workspace_id,
    )
    return len(specs)


def unregister_native_tools(
    workspace_id: str, connector_id: str, slug: Optional[str] = None
) -> None:
    """Drop native tool registrations for one connector.

    The canonical slug keys survive while any other row of the slug is
    still registered (e.g. uninstalling a personal row keeps the shared
    row's tools). ``slug`` is required for canonical bookkeeping; without
    it only the row bundle is removed.
    """
    from app.services.hooks.registry import unregister_bundle_registrations

    unregister_bundle_registrations(workspace_id, native_bundle_slug(connector_id))
    if not slug:
        return
    key = (workspace_id, slug)
    rows = _CANONICAL_ROWS.get(key)
    if rows is not None:
        rows.discard(connector_id)
        if rows:
            return
        _CANONICAL_ROWS.pop(key, None)
    unregister_bundle_registrations(workspace_id, canonical_bundle_slug(slug))


def reset_canonical_rows_for_tests() -> None:
    """Test helper — drop the in-process canonical refcounts."""
    _CANONICAL_ROWS.clear()


async def materialize_native_policies(*, connector: Any, actor_id: str) -> Any:
    """I-CON-04 for native connectors: grant connector subject tool.invoke.

    Reuses the MCP materializer — the grant shape is identical (connector
    subject × tool.invoke on its own scope); only the tool transport differs.
    """
    from app.agentive.connectors.mcp_mount import materialize_mcp_policies

    return await materialize_mcp_policies(connector=connector, actor_id=actor_id)


async def health_check_native_connector(connector: Any) -> Dict[str, Any]:
    """Probe Google with a lightweight call; persist health on the Connector."""
    import httpx

    from app.services.connectors.google_oauth import ensure_fresh_token

    slug = str(getattr(connector, "subclass_slug", "") or "")
    if slug not in NATIVE_GOOGLE_SLUGS:
        raise ValueError(f"health_check_native_connector: not native ({slug!r})")
    auth_snapshot = dict(getattr(connector, "auth_state", None) or {})
    if not auth_snapshot.get("refresh_token") and not auth_snapshot.get("access_token"):
        # Installed but sign-in never completed — not an error, just pending.
        result = {
            "status": "unknown",
            "error": "Finish sign-in to authorize this connector",
            "tool_count": 0,
        }
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
            "tool_count": 0,
        }
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            ok = await ensure_fresh_token(connector, http_client=http)
            if not ok:
                result = {
                    "status": "error",
                    "error": "Google authorization expired — re-authorize",
                    "tool_count": 0,
                }
            else:
                access_token = (getattr(connector, "auth_state", None) or {}).get(
                    "access_token"
                ) or ""
                # Drive scopes cover both Drive slugs; Gmail probes its own
                # profile endpoint (no Drive scope on mail grants).
                if slug == "gmail":
                    from app.agentive.connectors.gmail import GMAIL_REST_BASE

                    probe = await http.get(
                        f"{GMAIL_REST_BASE}/profile",
                        headers={"Authorization": f"Bearer {access_token}"},
                        params={"fields": "emailAddress"},
                    )
                else:
                    from app.agentive.connectors.drive_native import DRIVE_API_BASE

                    probe = await http.get(
                        f"{DRIVE_API_BASE}/files",
                        headers={"Authorization": f"Bearer {access_token}"},
                        params={"pageSize": 1, "fields": "files(id)"},
                    )
                if probe.status_code < 400:
                    result = {
                        "status": "ok",
                        "error": None,
                        "tool_count": _tool_count(slug),
                    }
                elif probe.status_code in (401, 403):
                    result = {
                        "status": "error",
                        "error": f"Google rejected the probe ({probe.status_code}) — check scopes / re-authorize",
                        "tool_count": 0,
                    }
                else:
                    result = {
                        "status": "degraded",
                        "error": f"Google probe returned {probe.status_code}",
                        "tool_count": 0,
                    }
    except Exception as exc:  # noqa: BLE001
        result = {"status": "error", "error": str(exc)[:300], "tool_count": 0}
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


def _tool_count(slug: str) -> int:
    return len(_tool_defs(slug))


async def disconnect_native_connector(connector: Any) -> bool:
    """Unregister workspace tools then delete the Connector Node."""
    from app.agentive.services.connector_registry_node import delete_connector

    workspace_id = getattr(connector, "workspace_id", "") or ""
    if workspace_id:
        unregister_native_tools(
            workspace_id,
            connector.id,
            str(getattr(connector, "subclass_slug", "") or ""),
        )
        try:
            from app.agentive.workspace_agent_profile import (
                invalidate_workspace_profile,
            )

            invalidate_workspace_profile(workspace_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "native_tool_registry: profile invalidate failed on disconnect"
            )
    return await delete_connector(connector.id)


async def rehydrate_native_connectors() -> None:
    """Re-register native Google tools for every native Connector at boot.

    Mirrors :func:`rehydrate_mcp_connectors`. Best-effort per connector —
    the specs are static first-party code, so rehydrate never needs the
    network (unlike MCP re-discovery).
    """
    from app.agentive.nodes import Connector

    try:
        connectors: List[Any] = []
        for slug in NATIVE_GOOGLE_SLUGS:
            try:
                found = await Connector.find({"subclass_slug": slug})
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "rehydrate_native_connectors: find failed for %s: %s", slug, exc
                )
                continue
            connectors.extend(found or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("rehydrate_native_connectors: find failed: %s", exc)
        return

    ok = 0
    for c in connectors:
        workspace_id = getattr(c, "workspace_id", "") or ""
        slug = str(getattr(c, "subclass_slug", "") or "")
        if not workspace_id or slug not in NATIVE_GOOGLE_SLUGS:
            continue
        try:
            register_native_tools(workspace_id, c.id, slug)
            try:
                await materialize_native_policies(
                    connector=c, actor_id=getattr(c, "owner", "") or ""
                )
            except Exception:  # noqa: BLE001 — tools registered; grant is extra
                logger.warning(
                    "rehydrate_native_connectors: policy heal failed for %s",
                    c.id,
                    exc_info=True,
                )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("rehydrate_native_connectors: failed for %s: %s", c.id, exc)
            try:
                c.health_status = "error"
                c.last_error = str(exc)[:500]
                c.last_health_at = utc_now_iso()
                await c.save()
            except Exception:  # noqa: BLE001
                pass
    logger.info("rehydrate_native_connectors: ok=%d", ok)
