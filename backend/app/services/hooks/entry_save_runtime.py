"""Entry-save hook dispatch — fires entry.create and entry.update bundle hooks.

Called after every entry create or update to let installed bundles run
post-save side effects. Hook failures are logged; an enclosing transaction
still rolls back failed nested writes. A hook saving its own record cannot
re-enter itself within the same task.
"""

from __future__ import annotations

import asyncio
import logging
import re as _re
from contextvars import ContextVar
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_active_save_hooks: ContextVar[frozenset[tuple[object, str, str, str]]] = ContextVar(
    "integral_active_save_hooks", default=frozenset()
)

_SLUG_RE = _re.compile(r"[^a-z0-9]+")


def _entry_type_slug(name: str) -> str:
    return _SLUG_RE.sub("_", str(name or "").strip().lower()).strip("_")


def entry_type_hook_slug(entry_type: Any) -> str:
    """Manifest entry type key for hook ``match.entry_type`` (falls back to slugified name)."""
    manifest_key = str(
        (getattr(entry_type, "form_schema", None) or {}).get("_manifest_entry_type_key")
        or ""
    ).strip()
    return manifest_key or _entry_type_slug(str(getattr(entry_type, "name", "") or ""))


async def _owning_bundle_slug(workspace_id: str, track_id: str) -> str:
    """Resolve the package from the Track's structural App edge, failing closed."""
    from app.models.nodes import Track

    track = await Track.get(track_id) if track_id else None
    if not track or str(track.workspace_id) != str(workspace_id):
        return ""
    owners = await track.nodes(
        edge=["CONTAINS"], node=["WorkspaceApp"], direction="in", limit=2
    )
    if len(owners) > 1:
        from app.api.errors import ResourceConflictError

        raise ResourceConflictError(
            message="This track has more than one owning App; its save rules cannot be determined."
        )
    if not owners:
        return ""
    if str(owners[0].workspace_id) != str(workspace_id):
        from app.api.errors import ResourceConflictError

        raise ResourceConflictError(
            message="The track and its owning App belong to different workspaces; its save rules cannot be determined."
        )
    # Seed writes occur while installation is in flight. A paused, failed or
    # uninstalled App must not inherit hooks from another active installation.
    if str(getattr(owners[0], "lifecycle_state", "active") or "active") not in {
        "active",
        "installing",
    }:
        return ""
    # Legacy installations predate the immutable package identity column.
    return str(
        getattr(owners[0], "installed_package_slug", "")
        or getattr(owners[0], "source_operational_model_slug", "")
        or ""
    )


async def _scoped_bindings(workspace_id: str, track_id: str, bindings: list) -> list:
    """Bundle hooks only run on entries belonging to that bundle's App."""
    if not bindings:
        return []
    slug = await _owning_bundle_slug(workspace_id, track_id)
    return [b for b in bindings if slug and b.get("_bundle_slug") == slug]


async def run_entry_before_write_hooks(
    *,
    workspace_id: str,
    actor_id: str,
    hook_point: str,
    entry_type_slug: str,
    entry_type_id: str,
    operation: str,
    custom_fields: Dict[str, Any],
    track_id: str = "",
    entry_id: str = "",
    write_actor: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch bundle hooks that validate or adjust fields before persistence.

    Bindings match on ``phase: before_write`` in their ``match`` block.
    Returns merged ``custom_fields`` (tools may return an updated dict).
    """
    from app.services.hooks.registry import ToolContext, get_workspace_tools
    from app.services.hooks.resolver import find_matching_bindings
    from app.services.hooks.tool_dispatch import run_tool

    merged_fields = dict(custom_fields or {})
    if not workspace_id or not entry_type_slug:
        return merged_fields

    payload = {
        "entry_type": entry_type_slug,
        "entry_type_id": entry_type_id,
        "entry_id": entry_id,
        "operation": operation,
        "actor_id": actor_id,
        "write_actor": write_actor or actor_id,
        "custom_fields": merged_fields,
        "phase": "before_write",
        "hook_point": hook_point,
    }

    tools = get_workspace_tools(workspace_id)
    ctx = ToolContext(
        user_id=actor_id,
        workspace_id=workspace_id,
        scope=f"entry:{entry_id}" if entry_id else f"entry_type:{entry_type_id}",
    )

    bindings = find_matching_bindings(workspace_id, hook_point, payload)
    for binding in await _scoped_bindings(workspace_id, track_id, bindings):
        if str(binding.get("mode") or "") != "tool":
            continue
        tool_key = str(binding.get("tool") or "")
        spec = tools.get(tool_key)
        if not spec:
            logger.warning(
                "entry_before_write: binding %r references missing tool %r (ws=%s)",
                binding.get("key"),
                tool_key,
                workspace_id,
            )
            continue
        result = await run_tool(spec, payload, ctx)
        if isinstance(result, dict):
            patch = result.get("custom_fields")
            if isinstance(patch, dict):
                merged_fields.update(patch)
                payload["custom_fields"] = merged_fields
    return merged_fields


async def run_entry_save_hooks(
    *,
    entry,
    workspace_id: str,
    actor_id: str,
    hook_point: str,
) -> None:
    """Dispatch bundle hooks for an entry create or update.

    Args:
        entry: The Entry node that was just saved.
        workspace_id: Workspace the entry belongs to.
        actor_id: User who performed the write (for ToolContext identity).
        hook_point: ``"entry.create"`` or ``"entry.update"``.
    """
    # Lazy imports — avoid circular import through registry → errors → api.__init__
    from app.models.nodes import EntryType
    from app.services.hooks.registry import ToolContext, get_workspace_tools
    from app.services.hooks.resolver import find_matching_bindings
    from app.services.hooks.tool_dispatch import run_tool

    if not workspace_id or not getattr(entry, "type_id", None):
        return

    et = await EntryType.get(entry.type_id)
    if not et:
        return

    # Prefer the manifest key (form_schema._manifest_entry_type_key) over a
    # slugified display name. hooks[].match.entry_type in operational-model.yaml is
    # always the manifest key — matching against the slugified NAME only
    # "worked" by coincidence for entry types whose display name happens to
    # slugify identically to their key. It silently never matches for entry
    # types named differently from their manifest key.
    slug = entry_type_hook_slug(et)
    payload = {
        "entry_type": slug,
        "entry_id": str(entry.id),
        "entry_type_id": str(entry.type_id),
        # Which of the two points fired. A tool bound to BOTH create and
        # update otherwise cannot tell them apart, and several want to —
        # "made this" and "came back to this" are different facts.
        "hook_point": hook_point,
        # OM hooks declare match.phase: after_write; omitting this silently
        # skips every post-save bundle side effect (HR employee hydration, etc.).
        "phase": "after_write",
    }

    tools = get_workspace_tools(workspace_id)
    ctx = ToolContext(
        user_id=actor_id,
        workspace_id=workspace_id,
        scope=f"entry:{entry.id}",
    )

    bindings = find_matching_bindings(workspace_id, hook_point, payload)
    for binding in await _scoped_bindings(
        workspace_id, str(getattr(entry, "track_id", "") or ""), bindings
    ):
        if str(binding.get("mode") or "") != "tool":
            continue
        tool_key = str(binding.get("tool") or "")
        spec = tools.get(tool_key)
        if not spec:
            logger.warning(
                "entry_save_runtime: binding %r references missing tool %r (ws=%s)",
                binding.get("key"),
                tool_key,
                workspace_id,
            )
            continue
        identity = (asyncio.current_task(), workspace_id, str(entry.id), tool_key)
        active = _active_save_hooks.get()
        if identity in active:
            continue
        from app.agentive.services.staging_execution import (
            assert_staging_hook_dispatch_allowed,
        )

        assert_staging_hook_dispatch_allowed()
        token = _active_save_hooks.set(active | {identity})
        try:
            await run_tool(spec, payload, ctx)
        except Exception:  # noqa: BLE001
            logger.exception(
                "entry_save_runtime: hook %r failed for entry %s (ws=%s)",
                binding.get("key"),
                entry.id,
                workspace_id,
            )
        finally:
            _active_save_hooks.reset(token)


async def run_entry_validation_hooks(
    *, workspace_id: str, actor_id: str, payload: dict
) -> None:
    """Run blocking validators against a proposed entry before persistence."""
    from app.services.hooks.errors import ToolValidationFailedError
    from app.services.hooks.registry import ToolContext, get_workspace_tools
    from app.services.hooks.resolver import find_matching_bindings
    from app.services.hooks.tool_dispatch import run_tool

    if not workspace_id:
        return
    tools = get_workspace_tools(workspace_id)
    ctx = ToolContext(
        user_id=actor_id, workspace_id=workspace_id, scope="entry:validate"
    )
    bindings = find_matching_bindings(workspace_id, "entry.validate", payload)
    for binding in await _scoped_bindings(
        workspace_id, str(payload.get("track_id") or ""), bindings
    ):
        if str(binding.get("mode") or "") != "tool":
            continue
        spec = tools.get(str(binding.get("tool") or ""))
        if not spec:
            raise ToolValidationFailedError(
                message=f"validation tool {binding.get('tool')!r} is not registered"
            )
        from app.agentive.services.staging_execution import (
            assert_staging_hook_dispatch_allowed,
        )

        assert_staging_hook_dispatch_allowed()
        result = await run_tool(spec, payload, ctx)
        if isinstance(result, dict) and result.get("ok") is False:
            raise ToolValidationFailedError(
                message=str(result.get("reason") or "Entry failed validation."),
                details={
                    "missing_compensation": result.get("missing_compensation") or [],
                    "entry_type": payload.get("entry_type"),
                },
            )
