"""Tool dispatcher — importlib handler resolution + JSON-schema validation."""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict

import jsonschema

from app.services.hooks.errors import (
    HookMisconfiguredError,
    ToolValidationFailedError,
)
from app.services.hooks.registry import ToolContext


def resolve_handler(handler_ref: str) -> Callable[..., Any]:
    """Resolve 'module.path:callable' to a Python callable.

    Bundle-relative paths must be normalized to absolute by the caller
    BEFORE this function (install_hook does that step).
    """
    if ":" not in handler_ref:
        raise HookMisconfiguredError(
            message=f"handler_ref must be 'module:callable' (got {handler_ref!r})"
        )
    module_path, fn_name = handler_ref.split(":", 1)
    try:
        mod = importlib.import_module(module_path)
    except Exception as exc:  # noqa: BLE001
        raise HookMisconfiguredError(
            message=f"failed importing {module_path}: {exc}",
            details={"handler_ref": handler_ref},
        ) from exc
    fn = getattr(mod, fn_name, None)
    if fn is None or not callable(fn):
        raise HookMisconfiguredError(
            message=f"{module_path}:{fn_name} is not a callable",
            details={"handler_ref": handler_ref},
        )
    return fn


def validate_input(payload: Dict[str, Any], schema: Dict[str, Any]) -> None:
    """Validate tool input ``payload`` against its declared JSON schema (no-op if empty)."""
    if not schema:
        return
    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ToolValidationFailedError(
            message=f"tool input schema mismatch: {exc.message}",
            details={"path": list(exc.path)},
        ) from exc


def validate_output(payload: Any, schema: Dict[str, Any]) -> None:
    """Validate tool output ``payload`` against its declared JSON schema (no-op if empty)."""
    if not schema:
        return
    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ToolValidationFailedError(
            message=f"tool output schema mismatch: {exc.message}",
            details={"path": list(exc.path)},
        ) from exc


async def run_tool(
    spec: Dict[str, Any],
    payload: Dict[str, Any],
    ctx: ToolContext,
) -> Any:
    """Execute a tool spec against payload + ToolContext.

    Validates input → resolves handler → awaits → validates output →
    emits audit ChangeEvent → returns output.

    MCP-mounted tools (ADR-009) carry ``_mcp_connector_id`` on the spec and
    route through ``mcp_proxy.invoke_from_spec`` so registry metadata reaches
    the proxy (a bare ``handler_ref`` call cannot see the spec).
    """
    handler_ref = str(spec.get("handler_ref") or "")
    in_schema = spec.get("parameters_schema") or spec.get("input_schema") or {}
    out_schema = spec.get("output_schema") or {}
    validate_input(payload, in_schema)

    if spec.get("_mcp_connector_id"):
        from app.agentive.connectors.mcp_proxy import invoke_from_spec

        result = await invoke_from_spec(spec, payload, ctx)
        validate_output(result, out_schema)
        # mcp_proxy emits tool.invoke ChangeEvent; skip duplicate ctx.emit_audit.
        return result

    fn = resolve_handler(handler_ref)
    # Stamp the calling bundle onto the context from the REGISTERED spec, so
    # a facade method can scope to "this bundle's own resources" without the
    # tool being able to claim it is another bundle (ADR-008). Set here rather
    # than at each ToolContext construction site so every dispatch path gets
    # it, and only ever from the registry's own metadata.
    ctx.bundle_slug = str(spec.get("_bundle_slug") or "")
    result = await fn(payload, ctx)
    validate_output(result, out_schema)
    await ctx.emit_audit(
        action="tool.invoke",
        details={"tool_key": spec.get("key"), "bundle_slug": spec.get("_bundle_slug")},
    )
    return result
