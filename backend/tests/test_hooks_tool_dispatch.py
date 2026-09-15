"""Tool dispatcher — importlib resolution + schema validation + trust gate."""

import pytest


def test_trust_gate_rejects_untrusted_with_tools():
    from app.services.hooks.errors import ToolTrustTierDeniedError
    from app.services.hooks.trust import check_tools_permitted

    with pytest.raises(ToolTrustTierDeniedError):
        check_tools_permitted(trust_tier="untrusted", tools_count=1, bundle_slug="x")
    # No tools declared → trust gate is a no-op even if untrusted.
    check_tools_permitted(trust_tier="untrusted", tools_count=0, bundle_slug="x")
    # Trusted always passes.
    check_tools_permitted(trust_tier="trusted", tools_count=5, bundle_slug="x")


def test_input_schema_validation_rejects_missing_required():
    from app.services.hooks.errors import ToolValidationFailedError
    from app.services.hooks.tool_dispatch import validate_input

    schema = {
        "type": "object",
        "required": ["x"],
        "properties": {"x": {"type": "integer"}},
    }
    with pytest.raises(ToolValidationFailedError):
        validate_input({}, schema)
    # Valid input passes silently.
    validate_input({"x": 7}, schema)


def test_resolve_handler_imports_callable(tmp_path, monkeypatch):
    # We test against a real bundle tool — but for unit test isolation,
    # exercise the resolver against a simple in-tree fixture module.
    from app.services.hooks.tool_dispatch import resolve_handler

    fn = resolve_handler("app.services.change_event:emit_change_event")
    assert callable(fn)


def test_resolve_handler_bad_format_raises():
    from app.services.hooks.errors import HookMisconfiguredError
    from app.services.hooks.tool_dispatch import resolve_handler

    with pytest.raises(HookMisconfiguredError):
        resolve_handler("no-colon-here")


@pytest.mark.asyncio
async def test_run_tool_validates_in_and_out(monkeypatch):
    from app.services.hooks.registry import ToolContext
    from app.services.hooks.tool_dispatch import run_tool

    # Stub tool spec pointing at an in-tree async no-op-ish callable.
    spec = {
        "key": "echo",
        "handler_ref": "tests.fixtures.tool_echo:echo",
        "parameters_schema": {
            "type": "object",
            "required": ["msg"],
            "properties": {"msg": {"type": "string"}},
        },
        "output_schema": {
            "type": "object",
            "properties": {"echoed": {"type": "string"}},
        },
    }
    ctx = ToolContext(user_id="u", workspace_id="w", scope="entry:e")

    # Avoid hitting DBLog logger in unit test — stub emit_audit.
    async def _noop_emit(action, details):  # type: ignore
        return None

    monkeypatch.setattr(ctx, "emit_audit", _noop_emit)
    out = await run_tool(spec, {"msg": "hello"}, ctx)
    assert out["echoed"] == "hello"
