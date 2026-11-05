"""Tool dispatcher — importlib handler resolution + JSON-schema validation."""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict

from collections import OrderedDict

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


# A tool's schema is the same dict on every call, but ``jsonschema.validate`` checks it against the
# metaschema each time, which dominated a record write (a pay run makes hundreds). Check it once and
# keep the validator. The cache holds the schema itself so its id cannot be reused by another object.
_VALIDATORS: "OrderedDict[int, tuple[Dict[str, Any], Any]]" = OrderedDict()
_MAX_VALIDATORS = 1024


def _validate_against(instance: Any, schema: Dict[str, Any]) -> None:
    """``jsonschema.validate`` with the schema check done once per schema object."""
    from jsonschema.exceptions import best_match
    from jsonschema.validators import validator_for

    cached = _VALIDATORS.get(id(schema))
    if cached is not None and cached[0] is schema:
        _VALIDATORS.move_to_end(id(schema))
        validator = cached[1]
    else:
        cls = validator_for(schema, default=jsonschema.Draft202012Validator)
        cls.check_schema(schema)
        validator = cls(schema)
        _VALIDATORS[id(schema)] = (schema, validator)
        while len(_VALIDATORS) > _MAX_VALIDATORS:
            _VALIDATORS.popitem(last=False)
    error = best_match(validator.iter_errors(instance))
    if error is not None:
        raise error


def validate_input(payload: Dict[str, Any], schema: Dict[str, Any]) -> None:
    """Validate tool input ``payload`` against its declared JSON schema (no-op if empty)."""
    if not schema:
        return
    try:
        _validate_against(payload, schema)
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
        _validate_against(payload, schema)
    except jsonschema.ValidationError as exc:
        raise ToolValidationFailedError(
            message=f"tool output schema mismatch: {exc.message}",
            details={"path": list(exc.path)},
        ) from exc


async def _run_tool(
    spec: Dict[str, Any],
    payload: Dict[str, Any],
    ctx: ToolContext,
) -> Any:
    """Execute a tool spec against payload + ToolContext.

    Validates input → resolves handler → awaits → validates output →
    emits audit ChangeEvent → returns output.

    MCP-mounted tools (ADR-009) carry ``_mcp_connector_id`` (or, for catalog
    connectors, ``_mcp_connector_slug``) on the spec and
    route through ``mcp_proxy.invoke_from_spec`` so registry metadata reaches
    the proxy (a bare ``handler_ref`` call cannot see the spec).
    """
    handler_ref = str(spec.get("handler_ref") or "")
    in_schema = spec.get("parameters_schema") or spec.get("input_schema") or {}
    out_schema = spec.get("output_schema") or {}
    validate_input(payload, in_schema)

    # Row-mounted MCP tools carry ``_mcp_connector_id``; catalog connectors (Google
    # Drive, ...) are slug-addressed and carry ``_mcp_connector_slug`` instead.
    # Both must reach the proxy with the spec, or its metadata is lost.
    if spec.get("_mcp_connector_id") or spec.get("_mcp_connector_slug"):
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
    # What THIS tool declared, stamped from the registered spec (a tool cannot grant itself more):
    # the connector tools it may write through, and whether it is a write tool at all.
    ctx.connector_writes = tuple(str(x) for x in (spec.get("connector_writes") or ()))
    ctx.tool_is_write = str(
        spec.get("side_effects") or "read_only"
    ).strip().lower() not in (
        "read",
        "read_only",
    )
    result = await fn(payload, ctx)
    validate_output(result, out_schema)
    await ctx.emit_audit(
        action="tool.invoke",
        details={"tool_key": spec.get("key"), "bundle_slug": spec.get("_bundle_slug")},
    )
    return result


async def run_tool(
    spec: Dict[str, Any],
    payload: Dict[str, Any],
    ctx: ToolContext,
) -> Any:
    """Invoke an extension and retain interrupted/failed staging outcomes."""
    from app.agentive.services.staging_execution import (
        begin_staging_hook,
        finish_staging_hook,
    )

    bound = begin_staging_hook()
    try:
        result = await _run_tool(spec, payload, ctx)
    except BaseException:
        finish_staging_hook(bound, failed=True)
        raise
    finish_staging_hook(
        bound,
        failed=isinstance(result, dict)
        and (result.get("error") is True or result.get("ok") is False),
    )
    return result
