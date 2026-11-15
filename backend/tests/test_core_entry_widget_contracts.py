"""Advertised entry widgets must compile after an ordinary cold startup."""

import os
import subprocess
import sys
import textwrap

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_entry_widgets_cold_start_preserves_config_and_bindings():
    # A fresh interpreter catches missing production registration without
    # borrowing a view type another test may have installed in the registry.
    script = textwrap.dedent(
        """
        from app.services.operational_model_plugins import discover_and_register_plugins
        from app.services.operational_model_compile import (
            compile_canonical_manifest, materialize_view_config_from_spec,
        )
        from app.views import operational_model_view_types as view_types

        discover_and_register_plugins()
        buttons = [{"key": "inspect", "label": "Inspect", "tool": "inspect_record"}]
        widgets = [
            ("action_bar", "entry", {"buttons": buttons}, {"buttons": buttons}),
            ("editable_table", "both", {"columns": ["quantity"]}, {}),
        ]
        for widget, scope, config, bindings in widgets:
            spec = view_types.get(widget)
            assert spec is not None, f"Advertised {widget} has no backend registration"
            assert spec.source == "plugin"
            assert spec.scope == scope
            assert config.keys() <= spec.config_schema.keys()
            for configuration in (config, {"config": config}):
                manifest = {
                    "operational_model_schema_version": 2,
                    "scope": "track",
                    "track": {
                        "views": [{
                            "key": "entry_widget", "name": "Entry widget",
                            "view_type": widget, "hidden": True, **configuration,
                        }],
                        "entry_types": [{
                            "name": "Item",
                            "fields": [{"key": "quantity", "name": "Quantity", "type": "number"}],
                            "related_views": [{"view": "entry_widget", "bind": bindings}],
                        }],
                    },
                }
                compiled = compile_canonical_manifest(manifest=manifest)["track"]
                view = compiled["views"][0]
                assert view["hidden"] is True
                persisted = materialize_view_config_from_spec(view)
                for key, value in config.items():
                    assert persisted[key] == value
                assert compiled["entry_types"][0]["related_views"][0]["bind"] == bindings
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "INTEGRAL_ENV_FILE": "/dev/null"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
