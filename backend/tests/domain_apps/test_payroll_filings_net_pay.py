"""Tests for the gross-to-net calculation
(``app/profiles/payroll-app/tools/_net_pay_calc.py`` — pure math — and
``tools/calculate_net_pay.py`` — the bundle-tool wrapper sourcing the
Compensation Record + Statutory Rates wiki pages).

Covers: progressive PAYE across bands, the NIS ceiling actually capping the
insurable wage, mid-period start (a compensation record or employee
start_date landing inside the period) prorating gross/NIS/PAYE together,
and zero gross producing a clean all-zero breakdown rather than an error.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.profiles.payroll_app.tools import _net_pay_calc as calc  # type: ignore[import]
from app.profiles.payroll_app.tools.calculate_net_pay import (
    calculate_net_pay,  # type: ignore[import]
)

PAYE_BANDS = [
    calc.PayeBand(from_amount=0, to_amount=130000, rate_pct=0),
    calc.PayeBand(from_amount=130000, to_amount=None, rate_pct=25),
]
NIS_CAP = calc.NisCap(monthly_ceiling=280000, employee_pct=5.6, employer_pct=8.4)
# These pure-proration/NIS-capping tests below aren't exercising GRA
# compliance at all (PAYE_BANDS above is a synthetic 0%/25% table, not the
# real GRA structure) — pass floor=0 so the (now-mandatory) personal
# allowance is a no-op and every pre-existing assertion in this section
# keeps holding.
NO_ALLOWANCE = calc.PersonalAllowance(floor=0)


# ── pure calc: monthly_equivalent (ported convention) ───────────────────────


@pytest.mark.parametrize(
    "base_salary,freq,expected",
    [
        (1200000, "annual", 100000),
        (100000, "monthly", 100000),
        (46154, "biweekly", pytest.approx(100000, abs=1)),
        (23077, "weekly", pytest.approx(100000, abs=1)),
    ],
)
def test_monthly_equivalent(base_salary, freq, expected):
    assert calc.monthly_equivalent(base_salary, freq) == expected


def test_calculate_net_pay_applies_guyana_overtime_deduction():
    result = calc.calculate_net_pay(
        base_salary=500000,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=REAL_PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=REAL_PERSONAL_ALLOWANCE,
        overtime=75000,
    )
    assert result["gross"] == 575000
    assert result["overtime_deduction"] == 50000
    assert result["govt_allowance"] == calc._bankers_round((575000 - 50000) / 3)


def test_calculate_net_pay_caps_medical_life_deduction_and_reduces_net():
    without = calc.calculate_net_pay(
        base_salary=500000,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=REAL_PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=REAL_PERSONAL_ALLOWANCE,
    )
    with_insurance = calc.calculate_net_pay(
        base_salary=500000,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=REAL_PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=REAL_PERSONAL_ALLOWANCE,
        medical_life_insurance=100000,
    )
    assert with_insurance["medical_life_insurance"] == 50000
    assert with_insurance["chargeable_income"] == without["chargeable_income"] - 50000
    assert with_insurance["net"] == pytest.approx(
        without["net"] - 50000 + (without["paye_tax"] - with_insurance["paye_tax"]),
        abs=0.01,
    )


# ── pure calc: PAYE bands ────────────────────────────────────────────────────


def test_paye_tax_zero_below_threshold():
    # Fully worked month, taxable income entirely within the 0% band.
    tax = calc.calc_paye_tax(100000, PAYE_BANDS, worked_days=30)
    assert tax == 0.0


def test_paye_tax_progressive_across_bands():
    # 200000 taxable: first 130000 @ 0%, remaining 70000 @ 25% = 17500.
    tax = calc.calc_paye_tax(200000, PAYE_BANDS, worked_days=30)
    assert tax == 17500.0


def test_paye_tax_open_ended_top_band():
    # Large income still resolves — open-ended band has no upper bound.
    tax = calc.calc_paye_tax(1000000, PAYE_BANDS, worked_days=30)
    assert tax == (1000000 - 130000) * 0.25


# ── pure calc: NIS cap ───────────────────────────────────────────────────────


def test_nis_contribution_under_ceiling():
    # Gross below the ceiling — contribution on the full gross.
    contrib = calc.calc_nis_employee_contribution(
        100000, NIS_CAP, worked_days=30, total_period_days=30
    )
    assert contrib == calc._bankers_round(100000 * 5.6 / 100)


def test_nis_contribution_capped_at_ceiling():
    # Gross above the 280000 ceiling — contribution capped at the ceiling,
    # NOT computed on the full (higher) gross.
    contrib = calc.calc_nis_employee_contribution(
        500000, NIS_CAP, worked_days=30, total_period_days=30
    )
    assert contrib == calc._bankers_round(280000 * 5.6 / 100)
    assert contrib < calc._bankers_round(500000 * 5.6 / 100)


def test_nis_employer_contribution_under_ceiling():
    contrib = calc.calc_nis_employer_contribution(
        100000, NIS_CAP, worked_days=30, total_period_days=30
    )
    assert contrib == calc._bankers_round(100000 * 8.4 / 100)


def test_nis_employer_contribution_capped_at_ceiling():
    contrib = calc.calc_nis_employer_contribution(
        500000, NIS_CAP, worked_days=30, total_period_days=30
    )
    assert contrib == calc._bankers_round(280000 * 8.4 / 100)
    assert contrib < calc._bankers_round(500000 * 8.4 / 100)


def test_nis_employer_contribution_zero_when_gross_zero():
    assert (
        calc.calc_nis_employer_contribution(
            0, NIS_CAP, worked_days=30, total_period_days=30
        )
        == 0.0
    )


# ── pure calc: full calculate_net_pay — mid-period start & zero gross ───────


def test_calculate_net_pay_full_month():
    result = calc.calculate_net_pay(
        base_salary=200000,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=NO_ALLOWANCE,
    )
    assert result["worked_days"] == 30
    assert result["gross"] == 200000.0
    assert result["nis_employee_contribution"] == calc._bankers_round(
        200000 * 5.6 / 100
    )
    assert result["net"] == calc._bankers_round(
        200000 - result["nis_employee_contribution"] - result["paye_tax"]
    )


def test_calculate_net_pay_full_31_day_month_does_not_inflate_monthly_salary():
    """A complete October is one monthly salary, not 31/30 of one."""
    result = calc.calculate_net_pay(
        base_salary=500000,
        pay_frequency="monthly",
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
        paye_bands=REAL_PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=REAL_PERSONAL_ALLOWANCE,
    )
    assert result["worked_days"] == 30
    assert result["period_days"] == 30
    assert result["gross"] == 500000.0


def test_calculate_net_pay_annual_salary_is_also_normalized_for_full_month():
    result = calc.calculate_net_pay(
        base_salary=6000000,
        pay_frequency="annual",
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
        paye_bands=REAL_PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=REAL_PERSONAL_ALLOWANCE,
    )
    assert result["gross"] == 500000.0


def test_calculate_net_pay_mid_period_start_prorates_everything():
    # Employee started on the 16th of a 30-day period -- exactly half.
    full = calc.calculate_net_pay(
        base_salary=200000,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=NO_ALLOWANCE,
    )
    half = calc.calculate_net_pay(
        base_salary=200000,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=NO_ALLOWANCE,
        employee_start_date=date(2026, 6, 16),
    )
    assert half["worked_days"] == 15
    assert half["gross"] == calc._bankers_round(full["gross"] / 2)
    # The NIS ceiling and PAYE bands scale down too, not just gross --
    # proportionally smaller contribution/tax, not the full-month figures
    # applied to a half-month wage.
    assert half["nis_employee_contribution"] < full["nis_employee_contribution"]


def test_calculate_net_pay_mid_period_start_prorates_personal_allowance_floor_too():
    # Same idea as the proration test above, but exercising the personal
    # allowance's own floor — it's a MONTHLY figure like the NIS ceiling and
    # PAYE band thresholds, so a half-month period must scale it down too,
    # not compare a half-month gross against the full $140,000 floor.
    allowance = calc.PersonalAllowance(floor=140000)
    half = calc.calculate_net_pay(
        base_salary=200000,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=REAL_PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=allowance,
        employee_start_date=date(2026, 6, 16),
    )
    assert half["worked_days"] == 15
    # Half-month gross is 100000; floor prorated to 15/30 = 70000; 1/3 of
    # 100000 = 33333.33 — the (prorated) floor wins.
    assert half["govt_allowance"] == calc._bankers_round(70000)


def test_calculate_net_pay_compensation_effective_after_period_start():
    # A raise/new compensation record effective mid-period gates pay the
    # same way an employee start_date does.
    result = calc.calculate_net_pay(
        base_salary=300000,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=NO_ALLOWANCE,
        compensation_effective_from=date(2026, 6, 21),
    )
    assert result["worked_days"] == 10
    assert result["gross"] == calc._bankers_round(300000 * 10 / 30)


def test_calculate_net_pay_zero_gross_when_base_salary_zero():
    result = calc.calculate_net_pay(
        base_salary=0,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=NO_ALLOWANCE,
    )
    assert result == {
        "gross": 0.0,
        "basic": 0.0,
        "bonus": 0.0,
        "overtime": 0.0,
        "overtime_deduction": 0.0,
        "medical_life_insurance": 0.0,
        "nis_employee_contribution": 0.0,
        "nis_employer_contribution": 0.0,
        "govt_allowance": 0.0,
        "chargeable_income": 0.0,
        "taxable_income": 0.0,
        "paye_tax": 0.0,
        "net": 0.0,
        "total_allowances": 0.0,
        "take_home": 0.0,
        "category": "employee",
        "worked_days": 30,
        "period_days": 30,
    }


# ── Guyana Payroll register redesign: real GRA rules ────────────────────────
#
# GRA's PERSONAL ALLOWANCE BUG (2026-09 fix). A prior "Guyana Payroll
# register redesign" session removed the personal allowance mechanism
# entirely (deleted calc_govt_allowance/calc_paye_employees_category/
# PersonalAllowanceRule), wrongly believing the paye_band table's own
# 0%-rate first band already covered it. Verified FALSE against GRA's own
# notice:
# https://gra.gov.gy/notice-to-employers-employees-self-employed-persons-revised-personal-allowance-and-deductions-for-income-tax-2026/
# — there is no 0% band at all; the personal allowance
# (max($140,000, gross/3)) is a real, separate subtraction from chargeable
# income, THEN taxed across exactly two bands (25% to $280,000/mo, 35%
# above). Every golden value below is reconciled to the cent against real
# V75 Inc. November 2025 payroll data
# (`/mnt/pool/DUMMY PAYROLL(Sheet1).csv`).

REAL_PAYE_BANDS = [
    calc.PayeBand(from_amount=0, to_amount=280000, rate_pct=25),
    calc.PayeBand(from_amount=280000, to_amount=None, rate_pct=35),
]
REAL_PERSONAL_ALLOWANCE = calc.PersonalAllowance(floor=140000)


def test_calc_personal_allowance_flat_floor_wins_for_lower_earners():
    # Arya Calhoun's shape: gross/3 (115378) < floor (140000) -- floor wins.
    allowance = calc.calc_personal_allowance(
        346134, REAL_PERSONAL_ALLOWANCE, worked_days=30
    )
    assert allowance == 140000


def test_calc_personal_allowance_one_third_wins_for_higher_earners():
    # Jack Fisher's shape: gross/3 (230601) > floor (140000) -- 1/3 wins.
    allowance = calc.calc_personal_allowance(
        691803, REAL_PERSONAL_ALLOWANCE, worked_days=30
    )
    assert allowance == pytest.approx(230601, abs=1)


def test_calculate_net_pay_employee_category_real_gra_rules_ceo_row():
    # Golden-value regression test under the REAL GRA rules — Jack Fisher,
    # V75 Inc. November 2025 payroll (CEO row): Basic 691803, 1 child
    # (10000/mo child allowance), six allowances summing 165000, full
    # calendar month. Reconciled to the cent (small rounding artifacts vs
    # the sheet's own display rounding, same convention as this repo's
    # other payroll golden tests).
    result = calc.calculate_net_pay(
        base_salary=691803,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=REAL_PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=REAL_PERSONAL_ALLOWANCE,
        category="employee",
        child_allowance=10000,
        allowances={
            "internet_allowance": 10000,
            "telephone_allowance": 20000,
            "traveling_allowance": 20000,
            "entertainment_allowance": 35000,
            "specialization_allowance": 40000,
            "responsibility_allowance": 40000,
        },
    )
    assert result["gross"] == 691803
    # Real GRA personal allowance: max(140000, 691803/3=230601) = 230601 --
    # matches the sheet's own "Gov't Allowance" column exactly.
    assert result["govt_allowance"] == 230601
    assert result["nis_employee_contribution"] == 15680
    assert result["nis_employer_contribution"] == 23520
    # chargeable_income = gross - nis - govt_allowance - child_allowance
    #                    = 691803 - 15680 - 230601 - 10000 = 435522
    assert result["chargeable_income"] == 435522
    # 25% up to 280000 (=70000), 35% on the remaining 155522 (=54432.70)
    # = 124432.70 -- sheet shows 124433 (its own cent-rounding).
    assert result["paye_tax"] == pytest.approx(124432.70, abs=0.5)
    assert result["net"] == pytest.approx(551690.30, abs=0.5)
    assert result["total_allowances"] == 165000
    assert result["take_home"] == pytest.approx(716690.30, abs=0.5)


def test_calculate_net_pay_regression_old_zero_band_formula_is_gone():
    # The OLD (buggy) formula treated the paye_band table's 0%-rate first
    # band (0-140000) as the personal allowance and subtracted NOTHING
    # separately -- i.e. it fed chargeable_income = gross - nis -
    # child_allowance straight into a 3-band table with a 0% first band.
    # Assert the new result does NOT match what that old formula would
    # have produced for a high earner (where the two formulas diverge the
    # most): the old formula would have taxed nearly all of gross at
    # 25%/35% with no allowance subtraction at all; the fixed formula
    # subtracts a real $230,601 allowance first.
    old_buggy_bands = [
        calc.PayeBand(from_amount=0, to_amount=140000, rate_pct=0),
        calc.PayeBand(from_amount=140000, to_amount=280000, rate_pct=25),
        calc.PayeBand(from_amount=280000, to_amount=None, rate_pct=35),
    ]
    old_chargeable = 691803 - 15680 - 10000  # gross - nis - child_allowance
    old_paye = calc.calc_paye_tax(old_chargeable, old_buggy_bands, worked_days=30)

    fixed = calc.calculate_net_pay(
        base_salary=691803,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=REAL_PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=REAL_PERSONAL_ALLOWANCE,
        category="employee",
        child_allowance=10000,
    )
    # Old (buggy) formula: 666123 chargeable (no allowance subtracted) ->
    # 0% on first 140000, 25% on next 140000 (=35000), 35% on the
    # remaining 386123 (=135143.05) = 170143.05 -- notably more PAYE than
    # the fixed formula's 124432.70 (chargeable is only 435522 after the
    # real $230,601 allowance subtraction).
    assert old_paye == pytest.approx(170143.05)
    assert fixed["paye_tax"] == pytest.approx(124432.70, abs=0.5)
    assert fixed["paye_tax"] < old_paye
    assert fixed["chargeable_income"] < old_chargeable


# Every other employee row from the same real V75 Inc. November 2025
# payroll sheet, reconciled to the cent by hand before being written here
# (see the task's reconciliation notes) -- gross, nis, nis_employer,
# child_allowance, sheet's own govt_allowance/chargeable/paye/net/
# take_home. All Employees-section rows; Consultants are covered
# separately below (they take a different, unaffected code path).
GOLDEN_EMPLOYEE_ROWS = [
    # name, gross, nis, nis_employer, child_allowance, deduction,
    # allowances, expected_govt_allowance, expected_chargeable,
    # expected_paye, expected_net, expected_take_home
    pytest.param(
        346134,
        15680,
        23520,
        20000,
        0,
        {
            "internet_allowance": 10000,
            "telephone_allowance": 20000,
            "traveling_allowance": 20000,
            "entertainment_allowance": 25000,
            "specialization_allowance": 30000,
            "responsibility_allowance": 40000,
        },
        140000,
        170454,
        42613.50,
        287840.50,
        432840.50,
        id="calhoun_arya_cto",
    ),
    pytest.param(
        202125,
        11319,
        16979,
        0,
        9286,
        {
            "internet_allowance": 25000,
            "telephone_allowance": 15000,
            "traveling_allowance": 20000,
            "entertainment_allowance": 20000,
            "specialization_allowance": 0,
            "responsibility_allowance": 15000,
        },
        140000,
        50806,
        12701.50,
        168818.50,
        263818.50,
        id="cummings_gary_exec_coordinator",
    ),
    pytest.param(
        241643,
        13532,
        20298,
        0,
        0,
        {
            "internet_allowance": 5000,
            "telephone_allowance": 15000,
            "traveling_allowance": 5000,
            "entertainment_allowance": 5000,
            "specialization_allowance": 26000,
            "responsibility_allowance": 0,
        },
        140000,
        88111,
        22027.75,
        206083.25,
        262083.25,
        id="davila_nylah_ai_engineer",
    ),
    pytest.param(
        241532,
        13526,
        20289,
        0,
        0,
        {
            "internet_allowance": 15000,
            "telephone_allowance": 15000,
            "traveling_allowance": 15000,
            "entertainment_allowance": 5000,
            "specialization_allowance": 30000,
            "responsibility_allowance": 0,
        },
        140000,
        88006,
        22001.50,
        206004.50,
        286004.50,
        id="tang_grey_software_developer",
    ),
    pytest.param(
        244885,
        13714,
        20570,
        0,
        0,
        {
            "internet_allowance": 15000,
            "telephone_allowance": 15000,
            "traveling_allowance": 5000,
            "entertainment_allowance": 5000,
            "specialization_allowance": 0,
            "responsibility_allowance": 0,
        },
        140000,
        91171,
        22792.75,
        208378.25,
        248378.25,
        id="roach_belle_ai_engineer",
    ),
    pytest.param(
        265931,
        14892,
        22338,
        0,
        0,
        {
            "internet_allowance": 10000,
            "telephone_allowance": 10000,
            "traveling_allowance": 5000,
            "entertainment_allowance": 5000,
            "specialization_allowance": 25000,
            "responsibility_allowance": 0,
        },
        140000,
        111039,
        27759.75,
        223279.25,
        278279.25,
        id="evans_caspian_software_developer",
    ),
    pytest.param(
        177144,
        9920,
        14880,
        0,
        0,
        {
            "internet_allowance": 10000,
            "telephone_allowance": 10000,
            "traveling_allowance": 5000,
            "entertainment_allowance": 5000,
            "specialization_allowance": 30000,
            "responsibility_allowance": 0,
        },
        140000,
        27224,
        6806.00,
        160418.00,
        220418.00,
        id="zimmerman_eliana_software_developer",
    ),
]


@pytest.mark.parametrize(
    "gross,nis,nis_employer,child_allowance,deduction,allowances,"
    "expected_govt_allowance,expected_chargeable,expected_paye,"
    "expected_net,expected_take_home",
    GOLDEN_EMPLOYEE_ROWS,
)
def test_calculate_net_pay_golden_values_v75_november_2025_payroll(
    gross,
    nis,
    nis_employer,
    child_allowance,
    deduction,
    allowances,
    expected_govt_allowance,
    expected_chargeable,
    expected_paye,
    expected_net,
    expected_take_home,
):
    """Every Employees-section row (besides the CEO row, its own dedicated
    test above) from the real V75 Inc. November 2025 payroll sheet
    (`/mnt/pool/DUMMY PAYROLL(Sheet1).csv`), reconciled to the cent
    against GRA's real formula. base_salary is set to ``gross`` directly
    (bonus_pct=0, taxable_deduction=0 for every one of these rows in the
    sheet) so this test exercises exactly the personal-allowance + PAYE-
    band math, not proration or the bonus/taxable_deduction paths already
    covered elsewhere."""
    result = calc.calculate_net_pay(
        base_salary=gross,
        pay_frequency="monthly",
        period_start=date(2025, 11, 1),
        period_end=date(2025, 11, 30),
        paye_bands=REAL_PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=REAL_PERSONAL_ALLOWANCE,
        category="employee",
        child_allowance=child_allowance,
        deduction=deduction,
        allowances=allowances,
    )
    assert result["gross"] == gross
    # Small cent-rounding tolerance throughout -- bankers-rounding (this
    # module's own convention, see _bankers_round) occasionally lands a
    # half-cent off the sheet's own (differently-rounded) display values,
    # same as this repo's other payroll golden tests.
    assert result["nis_employee_contribution"] == pytest.approx(nis, abs=0.5)
    assert result["nis_employer_contribution"] == pytest.approx(nis_employer, abs=0.5)
    assert result["govt_allowance"] == expected_govt_allowance
    assert result["chargeable_income"] == pytest.approx(expected_chargeable, abs=0.5)
    assert result["paye_tax"] == pytest.approx(expected_paye, abs=0.5)
    assert result["net"] == pytest.approx(expected_net, abs=0.5)
    assert result["take_home"] == pytest.approx(expected_take_home, abs=0.5)


# Consultants section of the same sheet (Russell, McGee, Kayleigh Tang,
# Mercado) -- Gross = Basic, everything statutory forced to 0, Net =
# Take-Home = Gross. This code path does not touch the personal allowance
# at all (see calculate_net_pay's consultant branch), so it is unaffected
# by the fix; included here for completeness against the same sheet.
@pytest.mark.parametrize(
    "gross",
    [
        pytest.param(500000, id="russell_alondra_qa_engineer_consultant"),
        pytest.param(300000, id="mcgee_weston_ai_engineer_consultant"),
        pytest.param(450000, id="tang_kayleigh_pm_consultant"),
        pytest.param(86618, id="mercado_rogelio_ai_engineer_consultant"),
    ],
)
def test_calculate_net_pay_golden_values_consultants_v75_november_2025_payroll(gross):
    result = calc.calculate_net_pay(
        base_salary=gross,
        pay_frequency="monthly",
        period_start=date(2025, 11, 1),
        period_end=date(2025, 11, 30),
        paye_bands=REAL_PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=REAL_PERSONAL_ALLOWANCE,
        category="consultant",
    )
    assert result["gross"] == gross
    assert result["govt_allowance"] == 0
    assert result["nis_employee_contribution"] == 0
    assert result["paye_tax"] == 0
    assert result["net"] == gross
    assert result["take_home"] == gross


def test_calculate_net_pay_consultant_category_zero_withholding():
    # Real sheet's Consultants section default: Gross = Basic + Bonus,
    # everything statutory forced to 0, Net = Take-Home = Gross straight.
    result = calc.calculate_net_pay(
        base_salary=500000,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=NO_ALLOWANCE,
        category="consultant",
    )
    assert result["gross"] == 500000
    assert result["govt_allowance"] == 0
    assert result["nis_employee_contribution"] == 0
    assert result["paye_tax"] == 0
    assert result["net"] == 500000
    assert result["take_home"] == 500000


def test_calculate_net_pay_consultant_manual_override_respected():
    # The real sheet's one exceptional consultant row (row 22) has a
    # hand-typed PAYE figure — manual_overrides must be respected, not
    # stomped back to 0, and must still flow into net/take-home.
    result = calc.calculate_net_pay(
        base_salary=500000,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=NO_ALLOWANCE,
        category="consultant",
        manual_overrides={"paye_tax": 5000},
    )
    assert result["paye_tax"] == 5000
    assert result["net"] == 500000 - 5000
    assert result["take_home"] == 500000 - 5000


def test_calculate_net_pay_zero_gross_when_not_yet_started():
    # Employee's start_date is AFTER the period ends entirely.
    result = calc.calculate_net_pay(
        base_salary=200000,
        pay_frequency="monthly",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        paye_bands=PAYE_BANDS,
        nis_cap=NIS_CAP,
        personal_allowance=NO_ALLOWANCE,
        employee_start_date=date(2026, 7, 1),
    )
    assert result["worked_days"] == 0
    assert result["gross"] == 0.0
    assert result["net"] == 0.0


# ── bundle-tool wrapper: sources from Compensation Record + wiki pages ─────


class _FakeEntry:
    def __init__(self, entry_id, custom_fields):
        self.id = entry_id
        self.custom_fields = custom_fields


class _FakeCtx:
    """Minimal ToolContext stand-in — only the methods calculate_net_pay uses."""

    def __init__(self, *, compensation, statutory_rates, employee=None):
        self._compensation = compensation
        self._statutory_rates = statutory_rates
        self._employee = employee

    async def find_entries_in_track_type(self, track_type):
        return {
            "Guyana Compensation Records": self._compensation,
            "Guyana Settings": self._statutory_rates,
        }.get(track_type, [])

    async def get_entry_system(self, entry_id):
        if self._employee and self._employee.id == entry_id:
            return self._employee
        return None


def _rate_pages():
    return [
        _FakeEntry(
            "band-1",
            {
                "_entry_type_slug": "paye_band",
                "from_amount": 0,
                "to_amount": 130000,
                "rate_pct": 0,
                "effective_date": "2026-01-01",
            },
        ),
        _FakeEntry(
            "band-2",
            {
                "_entry_type_slug": "paye_band",
                "from_amount": 130000,
                "to_amount": None,
                "rate_pct": 25,
                "effective_date": "2026-01-01",
            },
        ),
        _FakeEntry(
            "nis-cap",
            {
                "_entry_type_slug": "nis_contribution_cap",
                "monthly_ceiling": 280000,
                "employee_pct": 5.6,
                "employer_pct": 8.4,
                "effective_date": "2026-01-01",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_tool_wrapper_end_to_end():
    comp = _FakeEntry(
        "comp-1",
        {
            "employee": "emp-1",
            "base_salary": 200000,
            "pay_frequency": "monthly",
            "effective_date": "2026-01-01",
        },
    )
    ctx = _FakeCtx(compensation=[comp], statutory_rates=_rate_pages())
    result = await calculate_net_pay(
        {
            "employee_id": "emp-1",
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
        },
        ctx,
    )
    assert result["ok"] is True
    assert result["gross"] == 200000.0
    assert result["net"] < result["gross"]


@pytest.mark.asyncio
async def test_tool_wrapper_picks_latest_compensation_record_in_effect():
    older = _FakeEntry(
        "comp-old",
        {
            "employee": "emp-1",
            "base_salary": 150000,
            "pay_frequency": "monthly",
            "effective_date": "2025-01-01",
        },
    )
    newer = _FakeEntry(
        "comp-new",
        {
            "employee": "emp-1",
            "base_salary": 200000,
            "pay_frequency": "monthly",
            "effective_date": "2026-01-01",
        },
    )
    ctx = _FakeCtx(compensation=[older, newer], statutory_rates=_rate_pages())
    result = await calculate_net_pay(
        {
            "employee_id": "emp-1",
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
        },
        ctx,
    )
    assert result["gross"] == 200000.0


@pytest.mark.asyncio
async def test_tool_wrapper_no_compensation_record_is_clean_failure():
    ctx = _FakeCtx(compensation=[], statutory_rates=_rate_pages())
    result = await calculate_net_pay(
        {
            "employee_id": "emp-1",
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
        },
        ctx,
    )
    assert result["ok"] is False
    assert "compensation" in result["reason"]


@pytest.mark.asyncio
async def test_tool_wrapper_missing_input_is_clean_failure():
    ctx = _FakeCtx(compensation=[], statutory_rates=[])
    result = await calculate_net_pay({"employee_id": "emp-1"}, ctx)
    assert result["ok"] is False


def _seed_shaped_rate_pages():
    """Same rate pages as ``_rate_pages()`` but WITHOUT ``_entry_type_slug`` —
    the shape app.seeds actually produces (plant_seeds calls Entry.create()
    directly, bypassing validate_and_materialize_entry_custom_fields, so
    seed-planted rows never get that key). Real deploys populate
    statutory_rates via seeds, so this is the shape calculate_net_pay must
    actually handle in production, not just the enriched fake in
    _rate_pages()."""
    return [
        _FakeEntry(
            "band-1",
            {
                "from_amount": 0,
                "to_amount": 130000,
                "rate_pct": 0,
                "effective_date": "2026-01-01",
            },
        ),
        _FakeEntry(
            "band-2",
            {
                "from_amount": 130000,
                "to_amount": None,
                "rate_pct": 25,
                "effective_date": "2026-01-01",
            },
        ),
        _FakeEntry(
            "nis-cap",
            {
                "monthly_ceiling": 280000,
                "employee_pct": 5.6,
                "employer_pct": 8.4,
                "effective_date": "2026-01-01",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_tool_wrapper_handles_seed_planted_rate_pages_without_entry_type_slug():
    comp = _FakeEntry(
        "comp-1",
        {
            "employee": "emp-1",
            "base_salary": 200000,
            "pay_frequency": "monthly",
            "effective_date": "2026-01-01",
        },
    )
    ctx = _FakeCtx(compensation=[comp], statutory_rates=_seed_shaped_rate_pages())
    result = await calculate_net_pay(
        {
            "employee_id": "emp-1",
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
        },
        ctx,
    )
    assert result["ok"] is True
    assert result["gross"] == 200000.0
    assert result["net"] < result["gross"]
