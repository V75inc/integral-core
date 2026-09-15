"""Tool catalogue builder: the single tool-list source for the resident agent
and the future OAuth MCP server.

The catalogue advertises ONLY dispatchable tools (existing-status manifest
entries whose binding carries a live dispatch ref). It must never advertise a
tool that would return ``not_implemented`` — so the deferred propose tools
(``integral_workspace_setup`` / ``integral_onboard_user``, both bound with an
all-None ToolBinding) and the ``status: gap`` tools are excluded.
"""

from app.agentive.tooling.bindings import TOOL_BINDINGS
from app.agentive.tooling.catalogue import build_tool_catalogue
from app.agentive.tooling.manifest import load_manifest


def _by_name():
    """Build the catalogue once and index it by tool name."""
    return {entry["name"]: entry for entry in build_tool_catalogue()}


def test_every_array_param_declares_items():
    """Strict providers (OpenAI gpt-4.1 et al.) reject an ``array`` schema with
    no ``items``. Every array-typed tool parameter — top-level or nested — MUST
    declare ``items`` so the catalogue is portable across providers.

    Regression guard: ``integral_bulk_*`` ``entry_ids`` shipped as a bare
    ``type: array`` and jvagent logged a schema-validation warning at load.
    """

    def walk(node, path):
        offenders = []
        if isinstance(node, dict):
            if node.get("type") == "array" and "items" not in node:
                offenders.append(path)
            for key in ("properties", "items", "$defs", "definitions"):
                child = node.get(key)
                if isinstance(child, dict):
                    for k, v in child.items():
                        offenders += walk(v, f"{path}.{key}.{k}")
                elif isinstance(child, list):
                    for i, v in enumerate(child):
                        offenders += walk(v, f"{path}.{key}[{i}]")
        return offenders

    bad = []
    for entry in build_tool_catalogue():
        bad += walk(entry.get("input_schema") or {}, entry["name"])
    assert not bad, f"array params missing 'items': {bad}"


def test_catalogue_includes_wired_tools():
    """A read, a propose, and the share tool are advertised with full shape."""
    catalogue = _by_name()
    # A read tool, a propose tool, and the share convenience tool.
    assert "integral_list_tracks" in catalogue
    assert "integral_create_entry" in catalogue
    assert "integral_share" in catalogue

    ce = catalogue["integral_create_entry"]
    assert ce["op_class"] == "propose"

    for name in ("integral_list_tracks", "integral_create_entry", "integral_share"):
        entry = catalogue[name]
        assert entry["name"] == name
        assert isinstance(entry["description"], str) and entry["description"]
        schema = entry["input_schema"]
        assert isinstance(schema, dict)
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "op_class" in entry
        assert "policy_action" in entry


def test_catalogue_excludes_deferred():
    """Deferred (all-None binding) propose tools are not advertised."""
    catalogue = _by_name()
    # Both are existing-status but bound with an all-None ToolBinding (stager
    # is None) → not dispatchable → must not be advertised.
    assert "integral_workspace_setup" not in catalogue
    assert "integral_onboard_user" not in catalogue


def test_catalogue_excludes_gap_tools():
    """``status: gap`` tools are absent — only existing tools are advertised."""
    catalogue = _by_name()
    reg = load_manifest()
    gap_names = [n for n, t in reg.items() if t.status == "gap"]
    assert gap_names, "expected at least one gap tool in the manifest"
    # Pick a known gap tool and assert it is absent (only existing advertised).
    # (integral_get_scope was promoted gap -> existing by the skills-editor
    # audit; integral_bulk_move_entries is the sole remaining gap tool.)
    assert "integral_bulk_move_entries" in gap_names  # sanity: really a gap tool
    for name in gap_names:
        assert name not in catalogue, f"gap tool {name} must not be advertised"


def test_catalogue_input_schema_shape():
    """input_schema is an object schema carrying the manifest's declared params."""
    catalogue = _by_name()
    reg = load_manifest()
    entry = catalogue["integral_create_entry"]
    schema = entry["input_schema"]
    assert schema["type"] == "object"

    props = schema["properties"]
    required = schema.get("required", [])
    # The manifest's declared params drive the schema.
    spec_params = reg["integral_create_entry"].params
    for pname, pdef in spec_params.items():
        assert pname in props, f"declared param {pname} missing from schema"
        if isinstance(pdef, dict) and pdef.get("required") is True:
            assert pname in required, f"required param {pname} missing from required[]"
    # integral_create_entry declares ``text`` as required.
    assert "text" in props
    assert "text" in required


def test_catalogue_all_entries_dispatchable():
    """Every advertised entry has a binding with a live dispatch ref.

    Batch-control tools (begin/commit/cancel_batch) are the documented exception:
    they dispatch via a by-name intercept in ``_dispatch_propose`` (they need
    session_id from dispatch context), so their binding is an all-None sentinel.
    Intercepted-ephemeral tools (integral_propose_design, integral_ask_user) are
    the same documented exception: name-intercepted, all-None binding, no live
    dispatch ref by design.
    """
    from app.agentive.tooling.catalogue import _INTERCEPTED_EPHEMERAL_TOOLS
    from app.agentive.tooling.dispatch import _BATCH_CONTROL_TOOLS

    for entry in build_tool_catalogue():
        name = entry["name"]
        binding = TOOL_BINDINGS.get(name)
        assert binding is not None, f"{name} has no binding"
        if name in _BATCH_CONTROL_TOOLS or name in _INTERCEPTED_EPHEMERAL_TOOLS:
            continue  # name-intercepted; intentionally has no binding ref
        live = any(
            ref is not None
            for ref in (
                binding.handler_ref,
                binding.service_ref,
                binding.stager,
                binding.direct_ref,
            )
        )
        assert live, f"{name} advertised but has no live dispatch ref"
