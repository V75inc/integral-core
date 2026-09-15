"""Phase 17 — HR + Payroll manifest compile-validation tests.

Asserts both content-profile packages compile cleanly through the canonical
compiler, exercise the cross-App interlock (I-APP-03), and the ACC-08
``member`` field type resolves against the field-type registry.

These tests are the explicit Phase 17 blocker signal — if the ``member``
field test fails, ACC-08 has not landed in Phase 16 and Phase 17
cannot proceed (do not re-implement the field type here).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.exceptions import BadRequestError
from app.services.content_profile_compile import compile_canonical_manifest
from app.services.content_profile_field_types import allowed_keys, get
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.content_profile_plugins import (
    discover_and_register_plugins,
    reset_discovered_for_tests,
)
from app.views import content_profile_view_types as view_types

_REGION_SYSTEM_TYPES = [
    "form_region",
    "layout_container",
    "static_content",
    "tree_region",
    "chart_region",
    "summary_tiles",
]


@pytest.fixture(scope="module", autouse=True)
def _region_system_view_types():
    """Register region_system's plugin view types for this module's compiles.

    Phase 4 wires ``chart_region``/``tree_region`` views directly into
    hr_app/payroll-app manifests. Production registers these once at real
    app startup (``app/main.py``); test requests bypass the lifespan, so
    discovery must be triggered explicitly here — mirrors the identical
    pattern in ``test_payroll_filings_manifests.py``.
    """
    for key in _REGION_SYSTEM_TYPES:
        view_types._REGISTRY.pop(key, None)
    reset_discovered_for_tests()
    discover_and_register_plugins()
    yield
    for key in _REGION_SYSTEM_TYPES:
        view_types._REGISTRY.pop(key, None)


@pytest.fixture(scope="module")
def hr_compiled():
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next((s for s in specs if s.slug == "hr_app"), None)
    assert spec is not None, "hr_app profile missing from library"
    return compile_canonical_manifest(manifest=spec.manifest, scope_hint="app")


@pytest.fixture(scope="module")
def payroll_compiled():
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next((s for s in specs if s.slug == "payroll-app"), None)
    assert spec is not None, "payroll-app profile missing from library"
    return compile_canonical_manifest(manifest=spec.manifest, scope_hint="app")


# ---- HR-01 — HR App ------------------------------------------------------


def test_hr_app_declares_five_tracks(hr_compiled):
    """HR-01 — HR App ships employees / departments / requests / onboarding / performance."""
    track_keys = {t["key"] for t in hr_compiled["app"]["tracks"]}
    expected = {
        "employees",
        "departments",
        "requests",
        "onboarding",
        "performance",
    }
    missing = expected - track_keys
    assert not missing, f"HR App missing tracks: {missing}; have {track_keys}"


def test_hr_employee_carries_member_field(hr_compiled):
    """HR-01 + ACC-08 — employee.member field is type=member and required."""
    employees_track = next(
        t for t in hr_compiled["app"]["tracks"] if t["key"] == "employees"
    )
    employee_et = next(
        et for et in employees_track["entry_types"] if et["key"] == "employee"
    )
    member_field = next(
        (f for f in employee_et["fields"] if f["key"] == "member"), None
    )
    assert member_field is not None, "employee.member field missing"
    assert member_field["type"] == "member", member_field["type"]
    assert member_field.get("required") is True


def test_hr_requests_track_has_kanban_with_four_columns(hr_compiled):
    """HR-04 — requests_board kanban groups by status with 4 columns."""
    requests_track = next(
        t for t in hr_compiled["app"]["tracks"] if t["key"] == "requests"
    )
    kanban = next(
        (v for v in requests_track["views"] if v.get("view_type") == "kanban"),
        None,
    )
    assert kanban is not None
    assert kanban["group_by"] == "custom_fields.status"
    cols = {c["key"] for c in kanban["kanban_columns"]}
    assert cols == {"requested", "approved", "denied", "completed"}


def test_hr_employee_projects_relation_is_cross_app(hr_compiled):
    """HR-03 — Employee.projects is a cross-App many-relation into the Projects App.

    Phase 31 (DR-31-01 §6): Project entities migrated from
    ``crm-plus-pm-suite`` to the standalone **projects** bundle.
    """
    employees_track = next(
        t for t in hr_compiled["app"]["tracks"] if t["key"] == "employees"
    )
    employee_et = next(
        et for et in employees_track["entry_types"] if et["key"] == "employee"
    )
    projects_field = next(
        (f for f in employee_et["fields"] if f["key"] == "projects"), None
    )
    assert projects_field is not None
    rel = projects_field["relation"]
    # target_app matches the installed App.name (= the library package slug,
    # since content_profile_loader rewrites package.name → slug).
    assert rel.get("target_app") == "projects"
    assert rel.get("allow_cross_app") is True
    assert rel.get("many") is True


def test_hr_requires_projects_app_as_soft_dep(hr_compiled):
    """HR-03 — HR declares projects as optional requires_apps (soft dep).

    Phase 31 (DR-31-01 §6): the soft dep moved with the Project entity
    from ``crm-plus-pm-suite`` to the **projects** bundle.
    """
    reqs = hr_compiled["app"].get("requires_apps") or []
    projects_dep = next((r for r in reqs if r.get("key") == "projects"), None)
    assert projects_dep is not None
    assert projects_dep.get("optional") is True


# ---- Phase 4 — dashboard regions (chart_region / tree_region) -----------


def test_hr_employees_headcount_chart_view_compiles(hr_compiled):
    """Phase 4 — headcount_by_department is a valid chart_region view."""
    employees_track = next(
        t for t in hr_compiled["app"]["tracks"] if t["key"] == "employees"
    )
    chart = next(
        (v for v in employees_track["views"] if v["key"] == "headcount_by_department"),
        None,
    )
    assert chart is not None
    assert chart["view_type"] == "chart_region"
    assert chart["chart_type"] == "bar"
    assert chart["group_by"] == "custom_fields.department"
    assert chart["aggregate"] == "count"
    # Existing table view is untouched, not reordered/removed.
    table = next(
        (v for v in employees_track["views"] if v["key"] == "employees_table"), None
    )
    assert table is not None
    assert table["view_type"] == "table"


def test_hr_employees_org_chart_view_compiles(hr_compiled):
    """Phase 4 — org_chart is a valid tree_region view over the manager relation."""
    employees_track = next(
        t for t in hr_compiled["app"]["tracks"] if t["key"] == "employees"
    )
    tree = next((v for v in employees_track["views"] if v["key"] == "org_chart"), None)
    assert tree is not None
    assert tree["view_type"] == "tree_region"
    assert tree["parent_field"] == "custom_fields.manager"
    assert tree["label_field"] == "title"


def test_payroll_pay_runs_totals_chart_view_compiles(payroll_compiled):
    """Phase 4 — payroll_totals_chart is a valid chart_region view over pay_runs."""
    pay_runs_track = next(
        t for t in payroll_compiled["app"]["tracks"] if t["key"] == "pay_runs"
    )
    chart = next(
        (v for v in pay_runs_track["views"] if v["key"] == "payroll_totals_chart"),
        None,
    )
    assert chart is not None
    assert chart["view_type"] == "chart_region"
    assert chart["group_by"] == "custom_fields.pay_date"
    assert chart["y_field"] == "custom_fields.gross_total"
    assert chart["aggregate"] == "sum"
    # Existing table view is untouched, not reordered/removed.
    table = next(
        (v for v in pay_runs_track["views"] if v["key"] == "pay_runs_table"), None
    )
    assert table is not None
    assert table["view_type"] == "table"


# ---- HR-02 — Payroll App -------------------------------------------------


def test_payroll_app_declares_eight_tracks(payroll_compiled):
    """HR-02 — Payroll ships pay_runs / payroll_employees / compensation /
    payslips / settings (the Settings-menu consolidation — Pay Calendar,
    Statutory Rates, and Company Profile folded into one track, browsable
    as folders), plus filings (NIS/PAYE statutory filings — merged into
    this app from the former payroll_filings package; Guyana Payroll is now
    the one installable app covering pay runs, its own employee roster,
    compensation, payslips, and NIS/PAYE filings together).
    ``payroll_employees`` is this app's own roster track — Compensation
    Records link against it directly rather than hr_app, so Payroll runs
    standalone with hr_app now a soft dep."""
    tracks = payroll_compiled["app"]["tracks"]
    track_keys = {t["key"] for t in tracks}
    assert track_keys == {
        "pay_runs",
        "payroll_employees",
        "compensation",
        "payslips",
        "settings",
        "filings",
    }, track_keys
    settings_track = next(t for t in tracks if t["key"] == "settings")
    settings_et_keys = {et["key"] for et in settings_track["entry_types"]}
    assert settings_et_keys == {
        "settings_category",
        "pay_calendar",
        "paye_band",
        "nis_contribution_cap",
        "fiscal_allowances",
        "company_profile",
    }, settings_et_keys


def test_payroll_requires_hr_as_soft_dep(payroll_compiled):
    """HR-02 — Payroll declares hr_app as a soft (optional) requires_apps dep.

    Dep key matches the HR App's installed App.name, which derives from
    `package.slug` (`hr_app`) since content_profile_loader rewrites
    `package.name` to the slug at library-sync time. Payroll no longer
    hard-requires the HR App — Compensation Records link against Payroll's
    own Guyana Payroll Employees track, which the HR App's roster can optionally
    mirror into (see the ``sync_from_hrm`` setting) but never gates.
    """
    reqs = payroll_compiled["app"].get("requires_apps") or []
    hr = next((r for r in reqs if r.get("key") == "hr_app"), None)
    assert hr is not None
    assert hr.get("optional") is True


def test_compensation_employee_relation_targets_payroll_employees(payroll_compiled):
    """HR-03 — Compensation.employee is a same-App relation into Payroll's
    own Guyana Payroll Employees track, not a cross-App relation into HR."""
    comp_track = next(
        t for t in payroll_compiled["app"]["tracks"] if t["key"] == "compensation"
    )
    comp_et = next(
        et for et in comp_track["entry_types"] if et["key"] == "compensation_record"
    )
    emp_field = next((f for f in comp_et["fields"] if f["key"] == "employee"), None)
    assert emp_field is not None
    rel = emp_field["relation"]
    assert not rel.get("target_app")
    assert not rel.get("allow_cross_app")
    assert rel.get("allow_cross_track") is True
    assert rel.get("target_track_types") == ["payroll_employees"]


def test_payroll_employees_link_field_is_cross_app_soft(payroll_compiled):
    """The Guyana Payroll Employees roster's own ``hrm_source_employee_id`` field is
    the one legitimate cross-App relation left — an optional link back to
    the HR App, resolvable only once it's installed."""
    roster_track = next(
        t for t in payroll_compiled["app"]["tracks"] if t["key"] == "payroll_employees"
    )
    roster_et = next(
        et for et in roster_track["entry_types"] if et["key"] == "employee"
    )
    link_field = next(
        (f for f in roster_et["fields"] if f["key"] == "hrm_source_employee_id"),
        None,
    )
    assert link_field is not None
    rel = link_field["relation"]
    assert rel.get("target_app") == "hr_app"
    assert rel.get("allow_cross_app") is True


def test_payroll_seeds_statutory_rates_and_company_profile_only(payroll_compiled):
    """HR-02 — Payroll seeds the settings_category "folder" records, the
    statutory rate pages, and an empty Company Profile placeholder (merged
    in from the former payroll_filings package) — all onto the shared
    "settings" track (Settings-menu consolidation) — pay data itself is
    still entered post-install."""
    seeds = payroll_compiled["app"].get("seeds") or []
    seeded_tracks = {s["track"] for s in seeds}
    assert seeded_tracks == {"settings"}
    seeded_entry_types = {e.get("entry_type") for s in seeds for e in s["entries"]}
    assert seeded_entry_types == {
        "settings_category",
        "paye_band",
        "nis_contribution_cap",
        "fiscal_allowances",
        "company_profile",
    }


# ---- HR-05 — Seeds -------------------------------------------------------


def test_hr_seeds_five_departments(hr_compiled):
    """HR-05 — HR seeds Engineering / Design / Delivery / Operations / Leadership."""
    seeds = hr_compiled["app"].get("seeds") or []
    dept_seed = next((s for s in seeds if s["track"] == "departments"), None)
    assert dept_seed is not None
    titles = {e["title"] for e in dept_seed["entries"]}
    assert titles == {
        "Engineering",
        "Design",
        "Delivery",
        "Operations",
        "Leadership",
    }


def test_hr_seeds_sixteen_employees(hr_compiled):
    """HR-05 — HR seeds the 16-row roster (placeholder names)."""
    seeds = hr_compiled["app"].get("seeds") or []
    emp_seed = next((s for s in seeds if s["track"] == "employees"), None)
    assert emp_seed is not None
    assert len(emp_seed["entries"]) == 16, len(emp_seed["entries"])
    ids = {e.get("id") for e in emp_seed["entries"]}
    # Stable seed.id values per HR-05 idempotency contract (I-APP-04).
    assert all(i and i.startswith("seed-emp-") for i in ids), ids


# ---- ACC-08 — `member` field type --------------------------------------


def test_member_field_type_registered():
    """Phase 17 blocker signal — `member` must be in the field-type registry.

    If this test fails, ACC-08 has not landed and Phase 17 cannot
    proceed. Do not re-implement the field type here.
    """
    assert "member" in allowed_keys(), sorted(allowed_keys())
    spec = get("member")
    assert spec is not None
    assert spec.type == "member"


# ---- I-APP-03 — cross-App interlock negative test ---------------------------


def test_cross_app_interlock_rejected_without_allow_cross_app():
    """I-APP-03 — target_app set without allow_cross_app: true is rejected at compile.

    Mirrors the gate at content_profile_compile.py:328-333.
    """
    bad_manifest = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"slug": "bad-cross-app", "name": "Bad", "version": "1.0.0"},
        "app": {
            "tracks": [
                {
                    "key": "x",
                    "name": "X",
                    "entry_types": [
                        {
                            "key": "x_entry",
                            "name": "X entry",
                            "fields": [
                                {
                                    "key": "referent",
                                    "type": "relation",
                                    "relation": {
                                        "target_app": "hr_app",
                                        "target_entry_types": ["employee"],
                                        "target_track_types": ["employees"],
                                        # NOTE: allow_cross_app intentionally
                                        # omitted; allow_cross_track set to
                                        # satisfy the unrelated cross-track
                                        # validator. The compiler must still
                                        # reject this for the missing
                                        # allow_cross_app interlock.
                                        "allow_cross_track": True,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    }
    with pytest.raises(BadRequestError) as ei:
        compile_canonical_manifest(manifest=bad_manifest, scope_hint="app")
    assert "allow_cross_app" in str(ei.value) or "target_app" in str(ei.value)
