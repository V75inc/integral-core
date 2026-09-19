"""Tool catalogue builder (M2a Task 5 / "T6").

:func:`build_tool_catalogue` is the SINGLE tool-list source consumed by both
the resident agent surface (``IntegralAction.get_tools()``, next task) and the
future OAuth MCP server. It joins the manifest
:class:`~app.agentive.tooling.manifest.ToolSpec` (the contract) with the
:class:`~app.agentive.tooling.bindings.ToolBinding` (the wiring) and emits one
Anthropic-tool-shaped dict per tool that is BOTH:

* ``status == "existing"`` in the manifest (only tools the substrate genuinely
  backs are advertised — ``status: gap`` placeholders are not), AND
* **dispatchable** — its binding carries at least one live dispatch ref
  (``handler_ref`` / ``service_ref`` / ``stager`` / ``direct_ref``).

The dispatchability gate is the whole point: a consumer of the catalogue must
never see a tool that would return ``not_implemented`` from
:func:`~app.agentive.tooling.dispatch.dispatch_tool`. That excludes the deferred
propose tools (``integral_workspace_setup`` / ``integral_onboard_user``), each
bound with an all-None :class:`ToolBinding` (``stager=None``). It INCLUDES
``integral_set_focus`` (ephemeral context op, dispatched via ``direct_ref``) and
the attachment reads (``integral_list_attachments`` /
``integral_get_attachment_text``), bound to the ``attachment_agent`` service via
``service_ref``.

The emitted dict mirrors the Anthropic tool shape (``name`` / ``description`` /
``input_schema``) plus the two governance fields the resident surface and MCP
server gate on (``op_class`` / ``policy_action``). The manifest summaries are
already Anthropic-clean third-person prose, so ``description`` is ``spec.summary``
verbatim — no rewriting, no LLM, no schema inference beyond a faithful mapping
of the manifest ``params`` block into JSON Schema.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.agentive.tooling.bindings import TOOL_BINDINGS, ToolBinding
from app.agentive.tooling.manifest import DEFAULT_MANIFEST_PATH, ToolSpec, load_manifest

_CATALOGUE_CACHE_MTIME: Optional[float] = None
_CATALOGUE_CACHE_VALUE: Optional[List[Dict[str, Any]]] = None

# Manifest param ``type`` tokens -> JSON Schema ``type`` values. The manifest
# uses a small, JSON-Schema-adjacent vocabulary; ``integer`` is the only token
# that differs from its JSON Schema spelling here, but mapping the whole set
# explicitly keeps the translation honest (an unmapped token passes through so a
# future manifest type is not silently dropped).
_JSON_SCHEMA_TYPES = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
}

# Manifest param keys that carry their own meaning and are translated into the
# JSON Schema property explicitly; everything else on a param dict is treated as
# pass-through JSON Schema (so an already-JSON-Schema-shaped entry survives).
_MANIFEST_PARAM_KEYS = frozenset(
    {"type", "required", "optional", "default", "enum", "desc"}
)

# Ephemeral propose tools that dispatch via a by-NAME intercept in
# ``_dispatch_propose`` (they need ``session_id`` from dispatch context, which no
# binding ref carries), so their binding is an all-None sentinel
# (``ToolBinding(stager=None)``) exactly like ``_BATCH_CONTROL_TOOLS``. They ARE
# dispatchable and MUST be advertised — the ``stager``/``direct_ref`` gate below
# would wrongly exclude them, so they are allowed by name here.
_INTERCEPTED_EPHEMERAL_TOOLS = frozenset(
    {
        "integral_propose_design",
        "integral_ask_user",
        "integral_upsert_artifact",
        "integral_get_artifact",
        "integral_list_artifacts",
    }
)


def _is_dispatchable(name: str, binding: Optional[ToolBinding]) -> bool:
    """True if ``name`` has a binding with at least one live dispatch ref.

    A tool is dispatchable when it is registered in
    :data:`~app.agentive.tooling.bindings.TOOL_BINDINGS` AND its binding carries
    at least one of ``handler_ref`` / ``service_ref`` / ``stager`` /
    ``direct_ref`` (the four seams :func:`dispatch_tool` can route through). An
    all-None binding (the deferred ``propose`` orchestrations
    ``integral_workspace_setup`` / ``integral_onboard_user``) or a
    ``handler_ref=None`` read placeholder is NOT dispatchable — dispatching it
    returns ``not_implemented`` — so it must never be advertised.
    """
    # Batch-control tools (integral_begin/commit/cancel_batch) dispatch via a
    # by-NAME intercept in ``_dispatch_propose`` (they need session_id from
    # dispatch context), so their binding is an all-None sentinel. They ARE
    # dispatchable and MUST be advertised — without them the agent cannot open
    # or commit a batch.
    from app.agentive.tooling.dispatch import _BATCH_CONTROL_TOOLS

    if name in _BATCH_CONTROL_TOOLS or name in _INTERCEPTED_EPHEMERAL_TOOLS:
        return True
    if binding is None:
        return False
    return any(
        ref is not None
        for ref in (
            binding.handler_ref,
            binding.service_ref,
            binding.stager,
            binding.direct_ref,
        )
    )


def _param_to_property(pdef: Any) -> Dict[str, Any]:
    """Map one manifest param definition to a JSON Schema property object.

    The manifest declares each param as ``{type, required?/optional?, default?,
    enum?, desc?}`` (e.g. ``{type: string, required: true}`` or
    ``{type: integer, default: 20}``). This translates the manifest vocabulary
    into a JSON Schema property:

    * ``type`` -> the JSON Schema ``type`` (``integer`` stays ``integer``).
    * ``desc`` -> ``description``.
    * ``enum`` / ``default`` -> passed through verbatim.
    * ``required`` / ``optional`` are requiredness flags consumed by the caller
      (:func:`_build_input_schema`) for the schema-level ``required`` list, NOT
      property-level keys, so they are dropped here.

    If a param entry is already JSON-Schema-shaped (carries keys outside the
    manifest vocabulary, e.g. a nested ``properties`` / ``items``), those keys
    pass through untouched so a hand-authored schema survives. A non-dict param
    definition (defensive) yields a bare ``{}`` property.
    """
    prop: Dict[str, Any] = {}
    if not isinstance(pdef, dict):
        return prop

    raw_type = pdef.get("type")
    if isinstance(raw_type, str) and raw_type:
        prop["type"] = _JSON_SCHEMA_TYPES.get(raw_type, raw_type)

    if pdef.get("desc") is not None:
        prop["description"] = pdef["desc"]
    if "enum" in pdef:
        prop["enum"] = pdef["enum"]
    if "default" in pdef:
        prop["default"] = pdef["default"]

    # Pass through any already-JSON-Schema-shaped keys (properties, items,
    # description, etc.) that are NOT part of the manifest's own vocabulary, so
    # a hand-authored property is preserved rather than flattened.
    for key, value in pdef.items():
        if key == "required" and isinstance(value, list):
            prop[key] = value
            continue
        if key not in _MANIFEST_PARAM_KEYS and key not in prop:
            prop[key] = value

    return prop


def _build_input_schema(spec: ToolSpec) -> Dict[str, Any]:
    """Build a JSON Schema object from a tool spec's manifest ``params``.

    Always returns an ``object`` schema with a ``properties`` map (empty when
    the tool takes no params). A param is ``required`` when its manifest entry
    carries ``required: true`` or ``optional: false``; ``optional: true`` (and
    the absence of either) means optional. The ``required`` list is omitted
    when empty (a valid JSON Schema object with no required properties).
    """
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for pname, pdef in (spec.params or {}).items():
        properties[pname] = _param_to_property(pdef)
        if isinstance(pdef, dict) and (
            pdef.get("required") is True or pdef.get("optional") is False
        ):
            required.append(pname)

    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if spec.name == "integral_query_spec":
        schema["additionalProperties"] = False
    if required:
        schema["required"] = required
    return schema


def build_tool_catalogue() -> List[Dict[str, Any]]:
    """Build the advertised tool catalogue: one dict per dispatchable tool.

    Loads the manifest, filters to existing-status tools that have a
    dispatchable binding (see :func:`_is_dispatchable`), and emits the
    Anthropic-tool-shaped dict for each:

    ``{name, description, input_schema, op_class, policy_action}``

    The returned order follows the manifest's domain/tool declaration order
    (``load_manifest`` preserves it), giving a stable, human-meaningful
    grouping for both the resident agent and the MCP server.
    """
    global _CATALOGUE_CACHE_MTIME, _CATALOGUE_CACHE_VALUE
    path = DEFAULT_MANIFEST_PATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    if (
        _CATALOGUE_CACHE_VALUE is not None
        and mtime is not None
        and _CATALOGUE_CACHE_MTIME == mtime
    ):
        return _CATALOGUE_CACHE_VALUE

    registry = load_manifest()
    catalogue: List[Dict[str, Any]] = []
    for name, spec in registry.items():
        if spec.status != "existing":
            continue
        binding = TOOL_BINDINGS.get(name)
        if not _is_dispatchable(name, binding):
            continue
        catalogue.append(
            {
                "name": spec.name,
                "description": spec.summary,
                "input_schema": _build_input_schema(spec),
                "op_class": spec.op_class,
                "policy_action": spec.policy_action,
            }
        )
    _CATALOGUE_CACHE_MTIME = mtime
    _CATALOGUE_CACHE_VALUE = catalogue
    return catalogue
