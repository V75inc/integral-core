"""Tests for the create_wizard step-kind registry
(``content_profile_wizard_steps.py``) and its wiring into
``_normalize_create_wizard`` — the "bundle the wizard with region_system"
refactor. Confirms:

  - the registry itself (register/get/is_known/list_step_kinds);
  - region_system's plugin registers all four step kinds at discovery;
  - ``_normalize_create_wizard`` validates against the registry (rejects an
    unregistered kind, accepts a registered one) instead of a hardcoded
    tuple;
  - a plugin ``register()`` that does NOT declare ``wizard_step_registry``
    (payroll_filings, payroll_register) still loads fine — the
    introspection-based kwarg filtering in
    ``content_profile_plugins._register_plugin_module`` is backward
    compatible.
"""

from __future__ import annotations

import pytest

from app.exceptions import BadRequestError
from app.services import content_profile_wizard_steps as wizard_step_registry
from app.services.content_profile_compile import _normalize_create_wizard
from app.services.content_profile_plugins import discover_and_register_plugins


@pytest.fixture(scope="module", autouse=True)
def _discovered():
    discover_and_register_plugins()


def test_region_system_registers_all_four_builtin_step_kinds():
    kinds = wizard_step_registry.list_step_kinds()
    assert set(kinds) == {"form", "entry_checklist", "period_picker", "summary"}
    for kind, spec in kinds.items():
        assert spec.kind == kind
        assert spec.source == "plugin"


def test_registry_get_and_is_known():
    assert wizard_step_registry.is_known("form") is True
    assert wizard_step_registry.is_known("not_a_real_kind") is False
    assert wizard_step_registry.get("period_picker") is not None
    assert wizard_step_registry.get("not_a_real_kind") is None


def test_register_step_kind_rejects_duplicate_without_override():
    spec = wizard_step_registry.WizardStepKindSpec(kind="form", label="Form (dup)")
    with pytest.raises(ValueError):
        wizard_step_registry.register_step_kind(spec)
    # override=True is fine — same pattern register_view_type supports.
    wizard_step_registry.register_step_kind(spec, override=True)
    assert wizard_step_registry.get("form").label == "Form (dup)"
    # restore the real spec so later tests in this module aren't affected
    wizard_step_registry.register_step_kind(
        wizard_step_registry.WizardStepKindSpec(
            kind="form", label="Form", source="plugin"
        ),
        override=True,
    )


def test_normalize_create_wizard_accepts_registered_kinds():
    raw = {
        "steps": [
            {"kind": "form", "key": "f1", "fields": ["title"]},
            {"kind": "summary", "key": "confirm"},
        ],
        "on_create_tool": "some_tool",
    }
    result = _normalize_create_wizard(raw, where="test")
    assert result is not None
    assert [s["kind"] for s in result["steps"]] == ["form", "summary"]


def test_normalize_create_wizard_rejects_unregistered_kind():
    raw = {"steps": [{"kind": "not_a_real_kind", "key": "x"}]}
    with pytest.raises(BadRequestError):
        _normalize_create_wizard(raw, where="test")


def test_normalize_create_wizard_carries_entry_checklist_required_flag():
    """``required: true`` on an entry_checklist step must survive
    normalization — dropped otherwise, since this branch builds its output
    dict field-by-field rather than passing the raw spec through. Real
    trigger: payroll-app's "Select employees" step needed this to block
    creating a Pay Run with nobody on it when no Compensation Record
    matches the chosen pay frequency (a filtered-to-empty candidate list).
    Default (unset) stays absent — a checklist step may legitimately be
    optional (e.g. region-gallery's "link related records").
    """
    raw = {
        "steps": [
            {
                "kind": "entry_checklist",
                "key": "employees",
                "source_track_type": "Employees",
                "required": True,
            },
            {
                "kind": "entry_checklist",
                "key": "optional_links",
                "source_track_type": "Showcase Records",
            },
        ],
    }
    result = _normalize_create_wizard(raw, where="test")
    assert result["steps"][0]["required"] is True
    assert "required" not in result["steps"][1]


def test_plugin_without_wizard_step_registry_kwarg_still_loads():
    """payroll_filings/payroll_register declare `register(*, field_type_registry,
    view_type_registry)` with no `wizard_step_registry` param — the
    introspection-based kwarg filtering in content_profile_plugins.py must
    not error calling them. Covered implicitly by discover_and_register_plugins()
    succeeding in the module fixture above; this test just asserts their
    view types actually made it into the registry as proof registration
    genuinely ran end to end, not just didn't crash silently.
    """
    from app.views import content_profile_view_types as view_type_registry

    assert view_type_registry.is_known("payroll_register")
