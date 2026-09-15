"""Phase 1 (payroll-filings infra) — plugin-registered view types.

``backend/app/plugins/payroll_filings/__init__.py`` registers the
``editable_table`` and ``action_bar`` view types via the standard directory-
scan discovery (``content_profile_plugins._discover_via_directory``), which
production runs once at app startup (``app/main.py``'s ``_startup()``). Test
requests go through ``httpx.ASGITransport`` without a lifespan context (see
``tests/conftest.py``), so ``_startup()`` never fires for a test client —
call discovery directly against the real default plugin directory instead,
exactly mirroring what ``test_content_profile_plugins.py`` does for
tmp_path-based plugins.
"""

from app.services.content_profile_plugins import (
    discover_and_register_plugins,
    reset_discovered_for_tests,
)
from app.views import content_profile_view_types as view_types


def test_payroll_filings_plugin_registers_editable_table_and_action_bar():
    # Force a clean slate regardless of whether an earlier test in the same
    # process already triggered a real app boot (lifespan) that ran
    # discovery first — reset_discovered_for_tests() only clears the
    # _DISCOVERED descriptor cache, not the view-type _REGISTRY itself, so a
    # prior real registration would otherwise make register_view_type()
    # raise "already registered" on this call (caught internally, logged,
    # and silently dropped from the returned descriptor list) even though
    # the plugin's effect — the two view types being known — is unchanged.
    view_types._REGISTRY.pop("editable_table", None)
    view_types._REGISTRY.pop("action_bar", None)
    reset_discovered_for_tests()
    try:
        discover_and_register_plugins()

        assert view_types.is_known("editable_table")
        editable_table = view_types.get("editable_table")
        assert editable_table is not None
        assert editable_table.source == "plugin"

        assert view_types.is_known("action_bar")
        action_bar = view_types.get("action_bar")
        assert action_bar is not None
        assert action_bar.source == "plugin"
        assert "buttons" in action_bar.config_schema
    finally:
        view_types._REGISTRY.pop("editable_table", None)
        view_types._REGISTRY.pop("action_bar", None)
        reset_discovered_for_tests()
