"""Region-system infra (Phases 1 + 3.5 + 4) — plugin-registered view types.

``backend/app/plugins/region_system/__init__.py`` registers the
``form_region``, ``layout_container``, ``static_content``, ``tree_region``
(Phase 1), ``chart_region`` (Phase 3.5), ``summary_tiles`` (Phase 4), and
``reverse_relation_list`` (payroll structural redesign) view types via the
standard directory-scan discovery
(``operational_model_plugins._discover_via_directory``), which production runs
once at app startup (``app/main.py``'s ``_startup()``). Test requests go
through ``httpx.ASGITransport`` without a lifespan context (see
``tests/conftest.py``), so ``_startup()`` never fires for a test client — call
discovery directly against the real default plugin directory instead, exactly
mirroring ``test_payroll_filings_plugin.py``.
"""

from app.services.operational_model_plugins import (
    discover_and_register_plugins,
    reset_discovered_for_tests,
)
from app.views import operational_model_view_types as view_types

_REGION_TYPES = (
    "form_region",
    "layout_container",
    "static_content",
    "tree_region",
    "chart_region",
    "summary_tiles",
    "reverse_relation_list",
)


def test_region_system_plugin_registers_all_region_types():
    for key in _REGION_TYPES:
        view_types._REGISTRY.pop(key, None)
    reset_discovered_for_tests()
    try:
        discover_and_register_plugins()

        for key in _REGION_TYPES:
            assert view_types.is_known(key), f"{key} should be registered"
            spec = view_types.get(key)
            assert spec is not None
            assert spec.source == "plugin"

        form_region = view_types.get("form_region")
        assert "fields" in form_region.config_schema

        layout_container = view_types.get("layout_container")
        assert "regions" in layout_container.config_schema
        assert "mode" in layout_container.config_schema

        static_content = view_types.get("static_content")
        assert "body" in static_content.config_schema
        assert "body_field" in static_content.config_schema

        tree_region = view_types.get("tree_region")
        assert "source_track" in tree_region.config_schema
        assert "parent_field" in tree_region.config_schema

        chart_region = view_types.get("chart_region")
        assert "chart_type" in chart_region.config_schema
        assert "source" in chart_region.config_schema
        assert "aggregate" in chart_region.config_schema

        summary_tiles = view_types.get("summary_tiles")
        assert "tiles" in summary_tiles.config_schema

        reverse_relation_list = view_types.get("reverse_relation_list")
        assert "relation" in reverse_relation_list.config_schema
    finally:
        # Restore normal registry state rather than leaving these types
        # de-registered: other test modules in the same pytest process
        # compile manifests (hr_app/payroll-app/payroll_filings) that
        # reference layout_container/chart_region/etc. and would otherwise
        # fail with "Unsupported view type" for the rest of the session.
        for key in _REGION_TYPES:
            view_types._REGISTRY.pop(key, None)
        reset_discovered_for_tests()
        discover_and_register_plugins()
