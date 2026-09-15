"""Phase 30 — _parse_manifest_tools + _parse_manifest_hooks."""

import pytest

from app.services.content_profile_compile import (
    ContentProfileValidationError,
    _parse_manifest_hooks,
    _parse_manifest_tools,
)


def _make_tool(**overrides):
    base = {
        "key": "t1",
        "handler_ref": "tools.x:fn",
        "parameters_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }
    base.update(overrides)
    return base


def test_parse_tools_minimal():
    out = _parse_manifest_tools([_make_tool()], bundle_slug="x", trust_tier="trusted")
    assert len(out) == 1
    assert out[0]["key"] == "t1"


def test_parse_tools_trust_gate_blocks_untrusted_with_multiple_tools():
    with pytest.raises(ContentProfileValidationError) as exc:
        _parse_manifest_tools(
            [_make_tool(key="a"), _make_tool(key="b", handler_ref="tools.y:fn")],
            bundle_slug="sales",
            trust_tier="untrusted",
        )
    assert "trust_tier" in str(exc.value.message).lower()


def test_parse_tools_trust_gate_audited_tier_allows_tools():
    out = _parse_manifest_tools([_make_tool()], bundle_slug="x", trust_tier="audited")
    assert len(out) == 1


def test_parse_tools_missing_key_rejected():
    with pytest.raises(ContentProfileValidationError):
        _parse_manifest_tools(
            [
                {
                    "handler_ref": "tools.x:fn",
                    "parameters_schema": {},
                    "output_schema": {},
                }
            ],
            bundle_slug="x",
            trust_tier="trusted",
        )


def test_parse_tools_trust_gate_blocks_untrusted():
    with pytest.raises(ContentProfileValidationError):
        _parse_manifest_tools([_make_tool()], bundle_slug="x", trust_tier="untrusted")


def test_parse_tools_no_tools_is_ok_on_any_tier():
    assert _parse_manifest_tools([], bundle_slug="x", trust_tier="untrusted") == []


def test_parse_tools_duplicate_key_rejected():
    with pytest.raises(ContentProfileValidationError):
        _parse_manifest_tools(
            [_make_tool(key="t1"), _make_tool(key="t1")],
            bundle_slug="x",
            trust_tier="trusted",
        )


def test_parse_tools_missing_handler_ref_rejected():
    bad = {"key": "t1", "parameters_schema": {}, "output_schema": {}}
    with pytest.raises(ContentProfileValidationError):
        _parse_manifest_tools([bad], bundle_slug="x", trust_tier="trusted")


def test_parse_hooks_minimal():
    out = _parse_manifest_hooks(
        [
            {
                "point": "entry.transform",
                "key": "h1",
                "match": {"x": "y"},
                "mode": "declarative",
                "declarative": {},
            }
        ],
        declared_tool_keys=set(),
    )
    assert len(out) == 1
    assert out[0]["point"] == "entry.transform"


def test_parse_hooks_unknown_point_rejected():
    with pytest.raises(ContentProfileValidationError):
        _parse_manifest_hooks(
            [
                {
                    "point": "entry.bogus",
                    "key": "h1",
                    "mode": "declarative",
                    "declarative": {},
                }
            ],
            declared_tool_keys=set(),
        )


def test_parse_hooks_tool_mode_requires_declared_tool():
    with pytest.raises(ContentProfileValidationError):
        _parse_manifest_hooks(
            [
                {
                    "point": "entry.transform",
                    "key": "h1",
                    "mode": "tool",
                    "tool": "missing",
                }
            ],
            declared_tool_keys=set(),
        )
    # When the tool IS declared, it parses.
    out = _parse_manifest_hooks(
        [{"point": "entry.transform", "key": "h1", "mode": "tool", "tool": "present"}],
        declared_tool_keys={"present"},
    )
    assert out[0]["tool"] == "present"


def test_parse_hooks_declarative_requires_block():
    with pytest.raises(ContentProfileValidationError):
        _parse_manifest_hooks(
            [{"point": "entry.transform", "key": "h1", "mode": "declarative"}],
            declared_tool_keys=set(),
        )
