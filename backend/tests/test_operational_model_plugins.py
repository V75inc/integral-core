"""Tests for operational-model plugin discovery."""

import textwrap
from pathlib import Path

import pytest

from app.services import operational_model_field_types as field_types
from app.services.operational_model_plugins import (
    discover_and_register_plugins,
    discovered_plugins,
    reset_discovered_for_tests,
)
from app.views import operational_model_view_types as view_types


def _write_plugin(plugins_dir: Path, plugin_id: str, body: str) -> None:
    pkg = plugins_dir / plugin_id
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(body)


def test_discover_registers_directory_plugin(tmp_path: Path):
    reset_discovered_for_tests()
    body = textwrap.dedent(
        """
        from app.services.operational_model_field_types import FieldTypeSpec

        def register(*, field_type_registry, view_type_registry):
            field_type_registry.register_field_type(
                FieldTypeSpec(
                    type="test_plugin_field",
                    label="Test Plugin Field",
                    description="Registered by a directory plugin.",
                    source="plugin",
                ),
            )
        """
    )
    _write_plugin(tmp_path, "test_plugin_one", body)
    try:
        discover_and_register_plugins(directory=tmp_path)
        descriptors = discovered_plugins()
        assert any(d["id"] == "test_plugin_one" for d in descriptors)
        assert field_types.is_known("test_plugin_field")
    finally:
        # Cleanup global registries so other tests don't see the spec.
        field_types._REGISTRY.pop("test_plugin_field", None)
        reset_discovered_for_tests()


def test_discover_skips_files_and_hidden_dirs(tmp_path: Path):
    reset_discovered_for_tests()
    (tmp_path / "stray.py").write_text("# not a plugin")
    (tmp_path / "_internal").mkdir()
    discover_and_register_plugins(directory=tmp_path)
    descriptors = discovered_plugins()
    assert all(d["id"] not in {"stray", "_internal"} for d in descriptors)


def test_discover_is_idempotent(tmp_path: Path):
    reset_discovered_for_tests()
    body = textwrap.dedent(
        """
        from app.views.operational_model_view_types import ViewTypeSpec

        def register(*, field_type_registry, view_type_registry):
            view_type_registry.register_view_type(
                ViewTypeSpec(
                    type="test-plugin/view-idem",
                    label="Idempotent",
                    description="",
                    source="plugin",
                ),
            )
        """
    )
    _write_plugin(tmp_path, "test_plugin_idem", body)
    try:
        discover_and_register_plugins(directory=tmp_path)
        first = list(discovered_plugins())
        # Second call should not re-register or duplicate descriptors.
        discover_and_register_plugins(directory=tmp_path)
        second = list(discovered_plugins())
        assert first == second
    finally:
        view_types._REGISTRY.pop("test-plugin/view-idem", None)
        reset_discovered_for_tests()


def test_register_view_type_rejects_bare_plugin_type():
    """A NEW plugin-sourced view type must be namespaced 'pack-name/view-name'
    (docs/operational-models/UI_PACKS.md). Legacy bare names predating the
    standard are grandfathered via an explicit allowlist in
    ``operational_model_view_types.py`` — this asserts the general rule holds
    for anything not on that list."""
    with pytest.raises(ValueError, match="namespaced"):
        view_types.register_view_type(
            view_types.ViewTypeSpec(
                type="bare_type_not_namespaced",
                label="Bare",
                description="",
                source="plugin",
            )
        )
