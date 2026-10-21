"""Compiled handler references must recreate their external package namespace."""

import sys
from pathlib import Path

import pytest

from app.services.hooks.install_hook import _normalize_handler_ref
from app.services.hooks.tool_dispatch import resolve_handler

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.mark.parametrize(
    "reference",
    [
        "tools.handler:read",
        "app.packages.ci-external-fixture.tools.handler:read",
        "integral_bundle_ci_external_fixture.tools.handler:read",
    ],
)
def test_compiled_handler_recreates_external_namespace(
    tmp_path, monkeypatch, reference
):
    prefix = "integral_bundle_ci_external_fixture"
    monkeypatch.setitem(sys.modules, prefix, None)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "__init__.py").write_text("")
    (tools / "handler.py").write_text('def read():\n    return "current artifact"\n')
    try:
        normalized = _normalize_handler_ref(
            "ci-external-fixture", reference, bundle_dir=str(tmp_path)
        )
        assert normalized == f"{prefix}.tools.handler:read"
        assert resolve_handler(normalized)() == "current artifact"
        assert list(sys.modules[prefix].__path__) == [str(tmp_path)]
        assert (
            _normalize_handler_ref(
                "ci-external-fixture", normalized, bundle_dir=str(tmp_path)
            )
            == normalized
        )
    finally:
        for key in list(sys.modules):
            if key.startswith(prefix + "."):
                sys.modules.pop(key, None)


def test_foreign_namespace_is_not_rebased(tmp_path):
    reference = "integral_bundle_other_fixture.tools.handler:read"
    assert (
        _normalize_handler_ref(
            "ci-external-fixture", reference, bundle_dir=str(Path(tmp_path))
        )
        == reference
    )
