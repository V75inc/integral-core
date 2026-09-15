"""Phase 2 (payroll-filings port) — manifest compile-validation tests.

Asserts the ``payroll-filings`` content-profile package (NIS + PAYE) compiles
cleanly through the canonical compiler, declares a soft (optional) dependency
on ``hr_app`` (Payroll is decoupled from HR — see ``hrm_sync.py``), its
anchored child track_templates carry the new ``editable_table``/``action_bar``
views wired via ``related_views``, and its employee-line entry types relate
to Payroll's own Guyana Payroll Employees roster rather than duplicating identity
fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.content_profile_compile import compile_canonical_manifest
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.content_profile_plugins import (
    discover_and_register_plugins,
    reset_discovered_for_tests,
)
from app.views import content_profile_view_types as view_types


@pytest.fixture(scope="module", autouse=True)
def _payroll_filings_view_types():
    """Register editable_table/action_bar for this module's compiles.

    Production registers these once at real app startup (``app/main.py``);
    test requests bypass the lifespan (see ``tests/test_payroll_filings_plugin.py``),
    so discovery must be triggered explicitly here — and torn down after,
    mirroring that file's pattern, so this module doesn't leak registry
    state into other test modules in the same pytest session.
    """
    view_types._REGISTRY.pop("editable_table", None)
    view_types._REGISTRY.pop("action_bar", None)
    reset_discovered_for_tests()
    discover_and_register_plugins()
    yield
    view_types._REGISTRY.pop("editable_table", None)
    view_types._REGISTRY.pop("action_bar", None)
    reset_discovered_for_tests()


@pytest.fixture(scope="module")
def payroll_filings_compiled():
    """NIS/PAYE filings merged into payroll-app (no longer a separate
    payroll_filings package) — this fixture keeps its original name (most
    of this module's assertions are about the filings-specific manifest
    shape, unaffected by which package declares it) but now compiles the
    merged payroll-app package."""
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next((s for s in specs if s.slug == "payroll-app"), None)
    assert spec is not None, "payroll-app profile missing from library"
    return compile_canonical_manifest(manifest=spec.manifest, scope_hint="app")


@pytest.fixture(scope="module")
def hr_compiled():
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next((s for s in specs if s.slug == "hr_app"), None)
    assert spec is not None, "hr_app profile missing from library"
    return compile_canonical_manifest(manifest=spec.manifest, scope_hint="app")


def test_payroll_filings_requires_hr_app_softly(payroll_filings_compiled):
    """Payroll is decoupled from HR — hr_app is declared but as a soft
    (optional) dep; filing lines link against Payroll's own Payroll
    Employees roster, not hr_app, directly."""
    requires = payroll_filings_compiled["app"].get("requires_apps") or []
    hr_dep = next((r for r in requires if r.get("key") == "hr_app"), None)
    assert hr_dep is not None, "payroll-filings must declare requires_apps: hr_app"
    assert hr_dep.get("optional") is True


def test_payroll_filings_declares_one_merged_filings_track(payroll_filings_compiled):
    """nis_filings and paye_filings used to be two separate app.tracks[]
    entries — two sidebar destinations for what's conceptually one "go file
    NIS/PAYE" task. Merged into a single ``filings`` track holding both
    entry types (nis_schedule, paye_filing), switched via the track's own
    view-switcher instead of two nav entries — the documented ContentProfile
    modeling tenet for depth-via-mixed-types (see profile.yaml's ``filings``
    track description).

    Separately: nis_wage_ceilings / nis_contribution_rates were removed
    from app.tracks[] entirely — real Track+Entry data (genuinely read by
    nis_calc's rate lookups), but that put a compliance rate table that
    changes every few years in the same ambient navigation as the filings
    someone works with monthly. Folded into the ``statutory_rates`` wiki
    track's ``nis_contribution_cap`` pages instead (see
    tools/_statutory_rates.py's module docstring for why not
    app.settings_schema — no ToolContext settings-read accessor exists,
    and that's a core addition, out of scope for this app-only profile)."""
    tracks = payroll_filings_compiled["app"]["tracks"]
    track_keys = {t["key"] for t in tracks}
    assert "filings" in track_keys
    filings_track = next(t for t in tracks if t["key"] == "filings")
    entry_type_keys = {et["key"] for et in filings_track["entry_types"]}
    # paye_annual_return (Form 2) and paye_7b_batch (Form 7B) joined
    # nis_schedule/paye_filing here — same "one filings destination,
    # switched via view" reasoning, now covering the annual GRA filings
    # too, not just the monthly ones.
    assert entry_type_keys == {
        "nis_schedule",
        "paye_filing",
        "paye_annual_return",
        "paye_7b_batch",
    }
    view_keys = {v["key"] for v in filings_track["views"]}
    assert view_keys == {
        "feed",  # compiler-injected default view when none is marked is_default
        "schedule_header_form",
        "nis_schedules_table",
        "employer_information_form",
        "paye_summary_tiles",
        "paye_filings_table",
        "paye_annual_header_form",
        "paye_annual_summary_tiles",
        "paye_annual_returns_table",
        "paye_7b_header_form",
        "paye_7b_summary_tiles",
        "paye_7b_batches_table",
        # Payroll structural redesign: one status board shared by both
        # entry types (draft/generated/submitted/accepted), same idiom as
        # hr_app's requests_board. Now spans all four filing entry types.
        "filings_board",
    }


def test_payroll_filings_declares_two_track_templates(payroll_filings_compiled):
    """The NIS/PAYE line templates, plus payroll-app's own pre-existing
    pay-run-lines template now that both live in the same merged package."""
    template_keys = {
        t["key"] for t in payroll_filings_compiled["app"].get("track_templates", [])
    }
    assert {"nis-schedule-lines", "paye-filing-lines"} <= template_keys


def test_nis_schedule_anchors_lines_track(payroll_filings_compiled):
    nis_track = next(
        t for t in payroll_filings_compiled["app"]["tracks"] if t["key"] == "filings"
    )
    nis_schedule = next(
        et for et in nis_track["entry_types"] if et["key"] == "nis_schedule"
    )
    anchor_field = next(
        (f for f in nis_schedule["fields"] if f["key"] == "employee_lines_track"),
        None,
    )
    assert anchor_field is not None, "nis_schedule missing employee_lines_track anchor"
    assert anchor_field["type"] == "relation"
    relation = anchor_field["relation"]
    assert relation["target"] == "track"
    assert relation["target_track_template"] == "nis-schedule-lines"
    assert relation["auto_provision"] is True


def test_nis_schedule_related_views_are_primary(payroll_filings_compiled):
    nis_track = next(
        t for t in payroll_filings_compiled["app"]["tracks"] if t["key"] == "filings"
    )
    nis_schedule = next(
        et for et in nis_track["entry_types"] if et["key"] == "nis_schedule"
    )
    related = nis_schedule.get("related_views") or []
    assert {rv["view"] for rv in related} == {
        "schedule_header_form",
        ":anchored_track/lines_table",
        ":anchored_track/filing_actions",
    }
    assert all(rv["position"] == "primary" for rv in related)
    # Action buttons render first — same reasoning as PAYE's own order
    # check (test_paye_filing_related_views_include_employer_information_form).
    assert [rv["view"] for rv in related] == [
        ":anchored_track/filing_actions",
        "schedule_header_form",
        ":anchored_track/lines_table",
    ]


def test_nis_schedule_header_form_is_layout_container(payroll_filings_compiled):
    nis_track = next(
        t for t in payroll_filings_compiled["app"]["tracks"] if t["key"] == "filings"
    )
    header_view = next(
        v for v in nis_track["views"] if v["key"] == "schedule_header_form"
    )
    assert header_view["view_type"] == "layout_container"
    # Header (left, wider) and Pay Period Dates (right, narrower) side by
    # side on desktop, both full-width and stacked on tablet/mobile.
    assert header_view["config"]["mode"] == "grid"
    regions = header_view["config"]["regions"]
    region_keys = {r["key"] for r in regions}
    assert region_keys == {"header", "periods"}
    header_region = next(r for r in regions if r["key"] == "header")
    assert header_region["kind"] == "form"
    assert header_region["layout"]["span"]["desktop"] == 8
    assert header_region["fields"] == [
        "employer_name",
        "registration_number",
        # Payroll structural redesign: optional cross-app link to the
        # corresponding Pay Run + a status lifecycle (draft/generated/
        # submitted/accepted), same Header group as the rest.
        "pay_run",
        "status",
        "contribution_year",
        "contribution_month",
        "schedule_type",
    ]
    periods_region = next(r for r in regions if r["key"] == "periods")
    fields = periods_region["fields"]
    # Period 1 always shown (a bare key — no condition); 2-5 only when
    # Weekly, per-FIELD not per-region, since Period 1 must stay visible
    # either way (matches the original internal-tools webapp: Monthly
    # schedules only ever used Period 1). FormRegionWidget's own
    # isVisible/visible_if gate (frontend/src/components/views/
    # regionConditions.ts) is what actually hides fields client-side; this
    # only proves the condition survives compilation intact through to the
    # served config.
    assert fields[0] == "period_1_date"
    weekly_cond = {"field": "schedule_type", "equals": "Weekly"}
    assert [f["key"] for f in fields[1:]] == [
        "period_2_date",
        "period_3_date",
        "period_4_date",
        "period_5_date",
    ]
    assert all(f["visible_if"] == weekly_cond for f in fields[1:])


def test_paye_filing_related_views_include_employer_information_form(
    payroll_filings_compiled,
):
    paye_track = next(
        t for t in payroll_filings_compiled["app"]["tracks"] if t["key"] == "filings"
    )
    paye_filing = next(
        et for et in paye_track["entry_types"] if et["key"] == "paye_filing"
    )
    related = paye_filing.get("related_views") or []
    # paye_summary_tiles is no longer a standalone related_views entry — it's
    # nested as a kind:view region inside employer_information_form instead
    # (see below), sitting beside the company fields rather than further
    # down the page.
    assert {rv["view"] for rv in related} == {
        "employer_information_form",
        ":anchored_track/lines_table",
        ":anchored_track/filing_actions",
    }
    assert all(rv["position"] == "primary" for rv in related)
    # Action buttons render first — a preparer's first move is almost
    # always Generate/Import, not scrolling past the header and the whole
    # employee-line table to find them.
    assert [rv["view"] for rv in related] == [
        ":anchored_track/filing_actions",
        "employer_information_form",
        ":anchored_track/lines_table",
    ]

    header_view = next(
        v for v in paye_track["views"] if v["key"] == "employer_information_form"
    )
    assert header_view["view_type"] == "layout_container"
    assert header_view["config"]["mode"] == "grid"
    regions = header_view["config"]["regions"]
    employer_region = next(r for r in regions if r["key"] == "employer")
    assert employer_region["fields"] == [
        "company_name",
        "company_tin",
        "company_address",
        # Payroll structural redesign — see the identical addition on
        # nis_schedule's header region above.
        "pay_run",
        "status",
        "year",
        "period",
        "show_bank_details",
    ]
    # The live summary snapshot sits beside the fields, not below them.
    summary_region = next(r for r in regions if r["key"] == "summary")
    assert summary_region["kind"] == "view"
    assert summary_region["view"] == "paye_summary_tiles"


def test_paye_summary_tiles_view_config(payroll_filings_compiled):
    paye_track = next(
        t for t in payroll_filings_compiled["app"]["tracks"] if t["key"] == "filings"
    )
    tiles_view = next(
        v for v in paye_track["views"] if v["key"] == "paye_summary_tiles"
    )
    assert tiles_view["view_type"] == "summary_tiles"
    tiles = tiles_view["config"]["tiles"]
    assert [t["field"] for t in tiles] == [
        "total_entries",
        "total_income",
        "total_deductions",
        "total_tax",
    ]


def test_nis_schedule_line_has_mirrored_identity_fields(payroll_filings_compiled):
    template = next(
        t
        for t in payroll_filings_compiled["app"]["track_templates"]
        if t["key"] == "nis-schedule-lines"
    )
    line_et = next(
        et for et in template["entry_types"] if et["key"] == "nis_schedule_line"
    )
    field_keys = {f["key"] for f in line_et["fields"]}
    assert {"ssn", "surname", "first_name"} <= field_keys
    for key in ("ssn", "surname", "first_name"):
        field = next(f for f in line_et["fields"] if f["key"] == key)
        assert field["readonly"] is True


def test_nis_schedule_lines_has_editable_table_and_action_bar(
    payroll_filings_compiled,
):
    template = next(
        t
        for t in payroll_filings_compiled["app"]["track_templates"]
        if t["key"] == "nis-schedule-lines"
    )
    view_types = {v["key"]: v["view_type"] for v in template["views"]}
    assert view_types == {
        "lines_table": "editable_table",
        "filing_actions": "action_bar",
    }


def test_nis_filing_actions_bind_carries_button_specs(payroll_filings_compiled):
    nis_track = next(
        t for t in payroll_filings_compiled["app"]["tracks"] if t["key"] == "filings"
    )
    nis_schedule = next(
        et for et in nis_track["entry_types"] if et["key"] == "nis_schedule"
    )
    actions_rv = next(
        rv
        for rv in nis_schedule["related_views"]
        if rv["view"] == ":anchored_track/filing_actions"
    )
    buttons = actions_rv["bind"]["buttons"]
    button_keys = {b["key"] for b in buttons}
    assert button_keys == {
        "generate_txt",
        "export_xls",
        "import_txt",
        "populate_from_hr",
        "clear_all",
    }
    generate = next(b for b in buttons if b["key"] == "generate_txt")
    assert generate["tool"] == "nis_generate_txt"
    assert generate["produces_file"] is True
    assert generate["persist_as_attachment"] is True


def test_nis_lines_table_columns_are_nested_under_config(payroll_filings_compiled):
    """EditableTableWidget reads ``view.config.columns`` (not top-level
    ``view.columns``, which is a distinct, unrelated compiled key used by the
    ``table`` view type) — assert the YAML's ``config.columns`` block landed
    where the widget actually looks."""
    template = next(
        t
        for t in payroll_filings_compiled["app"]["track_templates"]
        if t["key"] == "nis-schedule-lines"
    )
    lines_view = next(v for v in template["views"] if v["key"] == "lines_table")
    columns = lines_view["config"]["columns"]
    assert columns[0] == "employee"
    assert columns[1:4] == ["ssn", "surname", "first_name"]
    assert "employer_contribution" in columns


def test_nis_schedule_line_relates_to_payroll_employees(payroll_filings_compiled):
    """Filing lines relate to Payroll's own Guyana Payroll Employees roster (a
    same-App relation) rather than hr_app directly, now that Payroll is
    decoupled from HR."""
    template = next(
        t
        for t in payroll_filings_compiled["app"]["track_templates"]
        if t["key"] == "nis-schedule-lines"
    )
    line_et = next(
        et for et in template["entry_types"] if et["key"] == "nis_schedule_line"
    )
    employee_field = next(f for f in line_et["fields"] if f["key"] == "employee")
    assert employee_field["type"] == "relation"
    relation = employee_field["relation"]
    assert not relation.get("target_app")
    assert "employee" in relation.get("target_entry_types", [])
    assert relation.get("target_track_types") == ["payroll_employees"]
    assert not relation.get("allow_cross_app")


def test_nis_schedule_line_has_computed_calculation_columns(
    payroll_filings_compiled,
):
    template = next(
        t
        for t in payroll_filings_compiled["app"]["track_templates"]
        if t["key"] == "nis-schedule-lines"
    )
    line_et = next(
        et for et in template["entry_types"] if et["key"] == "nis_schedule_line"
    )
    computed_keys = {f["key"] for f in line_et["fields"] if f["type"] == "computed"}
    assert computed_keys == {
        "total_actual_wages",
        "total_insurable_wages",
        "employer_contribution",
        "employee_contribution",
    }


def test_paye_filing_line_maps_31_source_columns(payroll_filings_compiled):
    template = next(
        t
        for t in payroll_filings_compiled["app"]["track_templates"]
        if t["key"] == "paye-filing-lines"
    )
    line_et = next(
        et for et in template["entry_types"] if et["key"] == "paye_filing_line"
    )
    field_keys = {f["key"] for f in line_et["fields"]}
    expected = {
        "employee",
        "tin",
        "employee_number",
        "first_name",
        "last_name",
        "other_names",
        "address",
        "pay_frequency",
        "period_employed",
        "employee_type",
        "primary_secondary_job",
        "value_7a",
        "total_overtime",
        "second_job_deduction",
        "overtime_deduction",
        "adjusted_7a",
        "value_7b",
        "value_7c_taxable",
        "value_7c_nontaxable",
        "total_income",
        "personal_allowance",
        "employee_nis_contribution",
        "medical_life_insurance",
        "children_deduction",
        "total_deductions",
        "tax_deducted",
        "date_of_birth",
        "bank_name",
        "account_no",
        "routing_transit",
        "child_declaration_no",
    }
    missing = expected - field_keys
    assert not missing, f"paye_filing_line missing fields: {missing}"


def test_nis_rate_tables_no_longer_in_manifest(payroll_filings_compiled):
    """The rate tables moved out of app.tracks[] entirely (see
    test_payroll_filings_declares_one_merged_filings_track) — no
    nis_wage_ceilings / nis_contribution_rates seed group should remain.
    (company_profile's own single-blank-record seed is legitimate — see
    test_company_profile_track_declared_with_seed — so this only checks
    the rate-table groups specifically, not that seeds is empty.) The rate
    data itself now lives as ``statutory_rates`` seed rows — see
    test_statutory_rates_seed_rows_declare_correct_entry_type below."""
    seeds = payroll_filings_compiled["app"].get("seeds") or []
    seed_tracks = {s["track"] for s in seeds}
    assert "nis_wage_ceilings" not in seed_tracks
    assert "nis_contribution_rates" not in seed_tracks


def test_statutory_rates_lives_alongside_filings_in_merged_package(
    payroll_filings_compiled,
):
    """statutory_rates (paye_band / nis_contribution_cap / fiscal_allowances)
    and the NIS/PAYE filings tracks now live in the SAME package (the
    former payroll_filings app merged into payroll-app) — see
    test_hr_payroll_manifests.py's test_payroll_app_declares_seven_tracks /
    test_payroll_seeds_statutory_rates_and_company_profile_only for the
    full track+seed assertions. One place to edit PAYE bands / NIS rates,
    right alongside the filings that read them.

    Statutory Rates itself now lives on the shared "settings" track (the
    Settings-menu consolidation folds Pay Calendar / Statutory Rates /
    Company Profile into one track, browsable as folders) rather than its
    own dedicated track — assert by entry type, not track key."""
    tracks = payroll_filings_compiled["app"]["tracks"]
    track_keys = {t["key"] for t in tracks}
    assert "settings" in track_keys
    assert "filings" in track_keys
    settings_track = next(t for t in tracks if t["key"] == "settings")
    settings_et_keys = {et["key"] for et in settings_track["entry_types"]}
    assert {
        "paye_band",
        "nis_contribution_cap",
        "fiscal_allowances",
    } <= settings_et_keys
    seeds = payroll_filings_compiled["app"].get("seeds") or []
    assert "settings" in {s["track"] for s in seeds}


def test_company_profile_track_declared_with_seed(payroll_filings_compiled):
    """ "Set the company info once" needs somewhere to set it: a real entry
    type, seeded with one blank record on install so it's there without
    hunting for where to create it (see carry_forward_filing_header, which
    reads from this record first). Lives on the shared "settings" track
    since the Settings-menu consolidation."""
    tracks = payroll_filings_compiled["app"]["tracks"]
    track_keys = {t["key"] for t in tracks}
    assert "settings" in track_keys
    settings_track = next(t for t in tracks if t["key"] == "settings")
    et = next(
        et for et in settings_track["entry_types"] if et["key"] == "company_profile"
    )
    field_keys = {f["key"] for f in et["fields"]}
    assert field_keys == {
        "company_name",
        "registration_number",
        "tin",
        "address",
        "logo",
        "category",
    }

    seeds = payroll_filings_compiled["app"].get("seeds") or []
    cp_seed = next(
        s
        for s in seeds
        if s["track"] == "settings"
        and any(e["id"] == "seed-company-profile" for e in s["entries"])
    )
    assert len(cp_seed["entries"]) == 1


def test_company_profile_is_declared_singleton(payroll_filings_compiled):
    """Company Profile is the one genuinely singleton, cadence-independent
    record per employer (unlike Pay Run — per-period, no single row is
    "the" right one — or Pay Calendar — one record per cadence, so
    potentially several). ``singleton: true`` is enforced generically at
    entry-create time in app/api/entries.py (I-SUBSTRATE-01: no domain
    name in substrate scope — the compiler just passes the flag through)."""
    tracks = payroll_filings_compiled["app"]["tracks"]
    settings_track = next(t for t in tracks if t["key"] == "settings")
    et = next(
        et for et in settings_track["entry_types"] if et["key"] == "company_profile"
    )
    assert et["singleton"] is True


def test_annual_filings_relate_back_to_company_profile(payroll_filings_compiled):
    """paye_annual_return (Form 2) / paye_7b_batch (Form 7B) each carry a
    real ``relation`` field (materializes a REFERENCES edge — see
    carry_forward_header.py's ``link_entry_relation`` call) back to
    company_profile, both keyed ``company_profile`` so the single
    ``company_profile_annual_filings`` reverse_relation_list panel (see
    test_company_profile_annual_filings_view below) picks up both entry
    types via one shared field_key -- the same pattern pay_run_filings uses
    for nis_schedule + paye_filing via the shared "pay_run" field key."""
    tracks = payroll_filings_compiled["app"]["tracks"]
    filings_track = next(t for t in tracks if t["key"] == "filings")
    for et_key in ("paye_annual_return", "paye_7b_batch"):
        et = next(et for et in filings_track["entry_types"] if et["key"] == et_key)
        field = next(f for f in et["fields"] if f["key"] == "company_profile")
        assert field["type"] == "relation"
        relation = field["relation"]
        assert relation["target_entry_types"] == ["company_profile"]
        assert relation["target_track_types"] == ["settings"]
        assert relation["many"] is False


def test_company_profile_annual_filings_view(payroll_filings_compiled):
    """The Company Profile page surfaces a read-only "Annual Filings" panel
    -- reverse_relation_list scoped to the ``company_profile`` field_key --
    mirroring pay_run_filings' pattern on Pay Run."""
    tracks = payroll_filings_compiled["app"]["tracks"]
    settings_track = next(t for t in tracks if t["key"] == "settings")
    views = {v["key"]: v for v in settings_track["views"]}
    assert "company_profile_annual_filings" in views
    view = views["company_profile_annual_filings"]
    assert view["view_type"] == "reverse_relation_list"
    assert view["relation"] == "company_profile"

    et = next(
        et for et in settings_track["entry_types"] if et["key"] == "company_profile"
    )
    related_view_keys = {rv["view"] for rv in et["related_views"]}
    assert "company_profile_annual_filings" in related_view_keys


def test_wave_1_payroll_tools_declared(payroll_filings_compiled):
    tools = payroll_filings_compiled["app"]["tools"]
    tool_keys = {t["key"] for t in tools}
    assert {
        "calculate_net_pay",
        "generate_payslips_for_pay_run",
        "generate_journal_summary_for_pay_run",
    } <= tool_keys


def test_statutory_rates_update_skill_declared(payroll_filings_compiled):
    skills = payroll_filings_compiled["app"].get("skills") or []
    skill_keys = {s["key"] for s in skills}
    assert "statutory_rates_update" in skill_keys


def test_pay_run_has_no_auto_populate_hook(payroll_filings_compiled):
    """Guyana Payroll register redesign: SUPERSEDES the earlier
    auto_populate_pay_run_lines_on_pay_run_create hook (unconditionally
    grabbed every active employee the moment a Pay Run was created).
    Celery-style flow instead: the Create Pay Run wizard gates population
    on the preparer's own employee-selection step via
    populate_pay_run_lines_for_employees; a Pay Run created outside the
    wizard starts empty, with populate_pay_run_lines_from_hr staying as a
    manual catch-up button instead of an automatic hook."""
    hooks = payroll_filings_compiled["app"].get("hooks") or []
    pay_run_create_hooks = [
        h
        for h in hooks
        if h.get("point") == "entry.create"
        and h.get("match", {}).get("entry_type") == "pay_run"
    ]
    assert pay_run_create_hooks == []
    tool_keys = {t["key"] for t in payroll_filings_compiled["app"].get("tools") or []}
    assert "populate_pay_run_lines_from_hr" in tool_keys
    assert "populate_pay_run_lines_for_employees" in tool_keys


def test_pay_run_line_recalc_hooks_declared(payroll_filings_compiled):
    hooks = payroll_filings_compiled["app"].get("hooks") or []
    keys = {h.get("key") for h in hooks}
    assert {"recalc_pay_run_line_on_create", "recalc_pay_run_line_on_update"} <= keys
    for h in hooks:
        if h.get("key") in (
            "recalc_pay_run_line_on_create",
            "recalc_pay_run_line_on_update",
        ):
            assert h["match"]["entry_type"] == "pay_run_line"
            assert h["tool"] == "recalc_pay_run_line"
    tool_keys = {t["key"] for t in payroll_filings_compiled["app"].get("tools") or []}
    assert "recalc_pay_run_line" in tool_keys


def test_hr_app_employee_has_legal_name_split_fields(hr_compiled):
    employees_track = next(
        t for t in hr_compiled["app"]["tracks"] if t["key"] == "employees"
    )
    employee_et = next(
        et for et in employees_track["entry_types"] if et["key"] == "employee"
    )
    field_keys = {f["key"] for f in employee_et["fields"]}
    assert {"legal_first_name", "legal_last_name", "legal_other_names"} <= field_keys
    # base_fields.title (Government Name) must be untouched — non-breaking add.
    assert employee_et["base_fields"]["title"]["label"] == "Government Name"
