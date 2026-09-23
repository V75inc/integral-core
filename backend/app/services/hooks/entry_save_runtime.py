"""Entry-save hook dispatch — fires entry.create and entry.update bundle hooks.

Called after every entry create or update to let installed bundles run
post-save side effects (e.g., HR leave-balance recalc). Best-effort:
hook failures are logged and swallowed — they MUST NOT roll back the
parent write.
"""

from __future__ import annotations

import logging
import re as _re

logger = logging.getLogger(__name__)

_SLUG_RE = _re.compile(r"[^a-z0-9]+")


def _entry_type_slug(name: str) -> str:
    return _SLUG_RE.sub("_", str(name or "").strip().lower()).strip("_")


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
    # slugify identically to their key (e.g. hr_app's "Time Off Request" ->
    # "time_off_request"). It silently never matches for any entry type
    # named differently from its key (e.g. payroll_filings' "Employee Line"
    # whose key is "nis_schedule_line"/"paye_filing_line") — found via live
    # testing: nis_calc_row never fired on row edits, no error, no log.
    manifest_key = str(
        (et.form_schema or {}).get("_manifest_entry_type_key") or ""
    ).strip()
    slug = manifest_key or _entry_type_slug(et.name)
    payload = {
        "entry_type": slug,
        "entry_id": str(entry.id),
        "entry_type_id": str(entry.type_id),
        # Which of the two points fired. A tool bound to BOTH create and
        # update otherwise cannot tell them apart, and several want to —
        # "made this" and "came back to this" are different facts.
        "hook_point": hook_point,
    }

    tools = get_workspace_tools(workspace_id)
    ctx = ToolContext(
        user_id=actor_id,
        workspace_id=workspace_id,
        scope=f"entry:{entry.id}",
    )

    for binding in find_matching_bindings(workspace_id, hook_point, payload):
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
        try:
            await run_tool(spec, payload, ctx)
        except Exception:  # noqa: BLE001
            logger.exception(
                "entry_save_runtime: hook %r failed for entry %s (ws=%s)",
                binding.get("key"),
                entry.id,
                workspace_id,
            )


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
    for binding in find_matching_bindings(workspace_id, "entry.validate", payload):
        if str(binding.get("mode") or "") != "tool":
            continue
        spec = tools.get(str(binding.get("tool") or ""))
        if not spec:
            raise ToolValidationFailedError(
                message=f"validation tool {binding.get('tool')!r} is not registered"
            )
        result = await run_tool(spec, payload, ctx)
        if isinstance(result, dict) and result.get("ok") is False:
            raise ToolValidationFailedError(
                message=str(result.get("reason") or "Entry failed validation."),
                details={
                    "missing_compensation": result.get("missing_compensation") or [],
                    "entry_type": payload.get("entry_type"),
                },
            )
