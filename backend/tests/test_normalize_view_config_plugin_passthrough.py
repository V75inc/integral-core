"""normalize_view_config must preserve config for plugin-registered view types.

Regression coverage for a bug where the named-key extraction in
``normalize_view_config`` silently dropped any config key it didn't
explicitly know about (e.g. layout_container's ``regions``, form_region's
``fields``) for every non-builtin view type. Builtin types (wiki/kanban/
table/...) only ever use the named keys, so their behavior is unchanged.

Phase 4 (hr_app/payroll-app ``chart_region``/``tree_region`` wiring) hit the
identical bug at two more call sites in the same file — the app-manifest
canonical compiler (``_normalize_view_spec``, exercised indirectly via
``compile_canonical_manifest``) and the declarative-view materializer
(``materialize_view_config_from_spec``) — both had their own fixed named-key
extraction lists that dropped flat top-level plugin-widget keys (e.g.
chart_region's ``chart_type``/``y_field``, tree_region's ``label_field``)
before ``normalize_view_config`` ever saw them. Fixed with the identical
generic-passthrough technique; covered here too.
"""

from app.services.content_profile_compile import (
    _normalize_view_spec,
    materialize_view_config_from_spec,
    normalize_view_config,
)
from app.services.content_profile_plugins import (
    discover_and_register_plugins,
    reset_discovered_for_tests,
)
from app.views import content_profile_view_types as view_type_registry


def _ensure_region_system_registered():
    # Test requests bypass the app lifespan (see test_region_system_plugin.py),
    # so plugin discovery never runs automatically here — trigger it directly.
    if view_type_registry.is_known("layout_container"):
        return
    reset_discovered_for_tests()
    discover_and_register_plugins()


def test_builtin_table_still_drops_unknown_keys():
    cfg = normalize_view_config(
        "table",
        {"columns": ["title"], "some_future_unknown_key": "should not survive"},
    )
    assert cfg["columns"] == ["title"]
    assert "some_future_unknown_key" not in cfg


def test_builtin_kanban_regression_unchanged():
    cfg = normalize_view_config(
        "kanban",
        {"group_by": "custom_fields.status", "totally_unknown": 123},
    )
    assert cfg["group_by"] == "custom_fields.status"
    assert "totally_unknown" not in cfg


def test_builtin_wiki_regression_unchanged():
    cfg = normalize_view_config(
        "wiki",
        {"parent_field": "custom_fields.parent", "totally_unknown": 123},
    )
    assert cfg["parent_field"] == "custom_fields.parent"
    assert "totally_unknown" not in cfg


def test_plugin_view_type_preserves_custom_config_keys():
    _ensure_region_system_registered()
    spec = view_type_registry.resolve("layout_container")
    assert spec is not None, "region_system plugin must be registered for this test"
    assert spec.source == "plugin"

    cfg = normalize_view_config(
        "layout_container",
        {
            "mode": "tabs",
            "title": "Schedule Header",
            "regions": [
                {"key": "header", "kind": "form", "fields": ["employer_name"]},
            ],
        },
    )
    assert cfg["mode"] == "tabs"
    assert cfg["title"] == "Schedule Header"
    assert cfg["regions"] == [
        {"key": "header", "kind": "form", "fields": ["employer_name"]},
    ]


def test_plugin_view_type_form_region_preserves_fields_key():
    _ensure_region_system_registered()
    spec = view_type_registry.resolve("form_region")
    assert spec is not None
    assert spec.source == "plugin"

    cfg = normalize_view_config(
        "form_region",
        {"fields": ["company_name", "company_tin"], "columns": 2},
    )
    assert cfg["fields"] == ["company_name", "company_tin"]
    assert cfg["columns"] == 2


def test_normalize_view_spec_builtin_table_regression_unchanged():
    out = _normalize_view_spec(
        {
            "key": "t",
            "view_type": "table",
            "columns": ["title"],
            "some_future_unknown_key": "should not survive",
        }
    )
    assert out["columns"] == ["title"]
    assert "some_future_unknown_key" not in out


def test_normalize_view_spec_preserves_chart_region_keys():
    _ensure_region_system_registered()
    out = _normalize_view_spec(
        {
            "key": "headcount",
            "view_type": "chart_region",
            "chart_type": "bar",
            "group_by": "custom_fields.department",
            "aggregate": "count",
            "title": "Headcount by Department",
        }
    )
    assert out["chart_type"] == "bar"
    assert out["group_by"] == "custom_fields.department"
    assert out["aggregate"] == "count"
    assert out["title"] == "Headcount by Department"


def test_normalize_view_spec_preserves_tree_region_keys():
    _ensure_region_system_registered()
    out = _normalize_view_spec(
        {
            "key": "org_chart",
            "view_type": "tree_region",
            "parent_field": "custom_fields.manager",
            "label_field": "title",
        }
    )
    assert out["parent_field"] == "custom_fields.manager"
    assert out["label_field"] == "title"


def test_materialize_view_config_builtin_table_regression_unchanged():
    cfg = materialize_view_config_from_spec(
        {
            "key": "t",
            "view_type": "table",
            "columns": ["title"],
            "some_future_unknown_key": "should not survive",
        }
    )
    assert cfg["columns"] == ["title"]
    assert "some_future_unknown_key" not in cfg


def test_materialize_view_config_preserves_chart_region_keys():
    _ensure_region_system_registered()
    cfg = materialize_view_config_from_spec(
        {
            "key": "totals",
            "view_type": "chart_region",
            "chart_type": "bar",
            "group_by": "custom_fields.pay_date",
            "y_field": "custom_fields.gross_total",
            "aggregate": "sum",
            "title": "Payroll Totals by Pay Run",
        }
    )
    assert cfg["chart_type"] == "bar"
    assert cfg["y_field"] == "custom_fields.gross_total"
    assert cfg["aggregate"] == "sum"
    assert cfg["title"] == "Payroll Totals by Pay Run"
