"""Structural/shape tests for BVI Payroll's pure ``calculate_net_pay``.

Unlike Aruba Payroll's own ``test_aruba_payroll_net_pay_calc.py``, there is
still NO real BVI payslip/tax-admin data for Payroll Tax — see
``_net_pay_calc.py``'s module docstring. Most tests below assert the calc
engine's STRUCTURE and internal arithmetic consistency given arbitrary rate
inputs, not a "real" golden figure.

Three exceptions reproduce real Celery-issued BVI payslips to the cent:
``test_calc_nhi_matches_real_celery_payslips`` (NHI's confirmed 3.75%/3.75%
split), ``test_calc_ssb_age_65_switch_matches_real_celery_payslip`` (the
confirmed age-65 SSB switch to a flat employer-only 0.50%), and
``test_calc_ssb_full_time_and_part_time_match_real_celery_payslips`` (the
confirmed full-time/part-time SSB split, keyed off ``employment_type``).
Only Payroll Tax remains a placeholder now.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.profiles.bvi_payroll.tools._net_pay_calc import (  # type: ignore[import]
    NHIRates,
    PayrollTaxClass,
    SSBRates,
    age_as_of,
    calc_nhi,
    calc_payroll_tax,
    calc_ssb,
    calculate_net_pay,
)

_SSB = SSBRates(
    employer_pct=4.5,
    employee_pct=4.5,
    insurable_wage_ceiling_monthly=3796.83,
)
_NHI = NHIRates(employer_pct=3.75, employee_pct=3.75, wage_ceiling_monthly=None)
_TAX_CLASS_SMALL = PayrollTaxClass(
    employer_class="small",
    employer_rate_pct=2.0,
    employee_rate_pct=8.0,
    remuneration_threshold_annual=10000.0,
    small_employer_exemption_threshold_annual=150000.0,
)
_TAX_CLASS_LARGE = PayrollTaxClass(
    employer_class="large",
    employer_rate_pct=6.0,
    employee_rate_pct=8.0,
    remuneration_threshold_annual=10000.0,
    small_employer_exemption_threshold_annual=150000.0,
)


def _run(base_salary, **kwargs):
    kwargs.setdefault("pay_frequency", "monthly")
    kwargs.setdefault("period_start", date(2026, 1, 1))
    kwargs.setdefault("period_end", date(2026, 1, 31))
    kwargs.setdefault("ssb_rates", _SSB)
    kwargs.setdefault("payroll_tax_class", _TAX_CLASS_SMALL)
    kwargs.setdefault("nhi_rates", _NHI)
    return calculate_net_pay(base_salary=base_salary, **kwargs)


def test_module_docstring_flags_placeholder_status():
    """The module MUST self-document that its rates/formula are placeholders
    — this is a required discipline per docs/backend/payroll-apps-design.md,
    not an optional nicety."""
    import app.profiles.bvi_payroll.tools._net_pay_calc as calc_module

    doc = calc_module.__doc__ or ""
    assert "PLACEHOLDER" in doc
    assert "do not use for real payroll" in doc.lower()


def test_every_rate_is_a_parameter_never_a_hardcoded_constant():
    """Same discipline as Aruba's SocialSecurityRates/WageTaxBand — calling
    with wildly different rate objects must produce correspondingly
    different results, proving nothing is hardcoded."""
    cheap = SSBRates(
        employer_pct=1.0, employee_pct=1.0, insurable_wage_ceiling_monthly=None
    )
    expensive = SSBRates(
        employer_pct=20.0, employee_pct=20.0, insurable_wage_ceiling_monthly=None
    )
    r_cheap = _run(3000.0, ssb_rates=cheap)
    r_expensive = _run(3000.0, ssb_rates=expensive)
    assert r_cheap["ssb_employee"] != r_expensive["ssb_employee"]
    assert r_cheap["net"] > r_expensive["net"]


def test_gross_to_net_arithmetic_is_internally_consistent():
    r = _run(3000.0)
    expected_net = round(
        r["gross"]
        - r["ssb_employee"]
        - r["payroll_tax_employee"]
        - r["nhi_employee"]
        - r["other_deduction"],
        2,
    )
    assert r["net"] == pytest.approx(expected_net, abs=0.01)


def test_ssb_insurable_wage_ceiling_caps_the_base():
    r = calc_ssb(10000.0, _SSB)
    assert r["insurable_wage"] == 3796.83
    assert r["employee"] == pytest.approx(3796.83 * 0.045, abs=0.01)
    r_uncapped = calc_ssb(1000.0, _SSB)
    assert r_uncapped["insurable_wage"] == 1000.0


def test_nhi_with_no_ceiling_uses_full_gross():
    r = calc_nhi(50000.0, _NHI)
    assert r["covered_wage"] == 50000.0


def test_nhi_with_a_ceiling_caps_the_base():
    capped = NHIRates(employer_pct=3.75, employee_pct=3.75, wage_ceiling_monthly=2000.0)
    r = calc_nhi(5000.0, capped)
    assert r["covered_wage"] == 2000.0


def test_payroll_tax_employee_portion_only_applies_above_threshold():
    # threshold_monthly = 10000/12 ≈ 833.33
    r_below = calc_payroll_tax(500.0, _TAX_CLASS_SMALL)
    assert r_below["employee"] == 0.0
    r_above = calc_payroll_tax(5000.0, _TAX_CLASS_SMALL)
    assert r_above["employee"] > 0.0


def test_payroll_tax_employer_exemption_zeroes_employer_portion():
    r_exempt = calc_payroll_tax(
        5000.0, _TAX_CLASS_SMALL, employer_payroll_tax_exempt=True
    )
    assert r_exempt["employer"] == 0.0
    r_not_exempt = calc_payroll_tax(
        5000.0, _TAX_CLASS_SMALL, employer_payroll_tax_exempt=False
    )
    assert r_not_exempt["employer"] > 0.0


def test_payroll_tax_class_selects_correct_tier():
    r_small = calc_payroll_tax(5000.0, _TAX_CLASS_SMALL)
    r_large = calc_payroll_tax(5000.0, _TAX_CLASS_LARGE)
    # Different employer_rate_pct between tiers must produce different
    # employer-portion figures given the same gross.
    assert r_small["employer"] != r_large["employer"]


def test_svb_style_three_categories_are_all_present_and_split_employer_employee():
    """Structural check that all three BVI deduction categories (SSB,
    Payroll Tax, NHI) are modeled with BOTH an employer and employee side —
    per the design constraint, not any confirmed real split."""
    r = _run(3000.0)
    for prefix in ("ssb", "payroll_tax", "nhi"):
        assert f"{prefix}_employee" in r
        assert f"{prefix}_employer" in r


def test_full_calendar_month_does_not_inflate_gross():
    r = _run(3000.0, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    assert r["gross"] == 3000.00


def test_zero_base_salary_returns_zeroed_breakdown():
    r = _run(0.0)
    assert r["gross"] == 0.0
    assert r["net"] == 0.0
    for key in (
        "ssb_employee",
        "ssb_employer",
        "payroll_tax_employee",
        "payroll_tax_employer",
        "nhi_employee",
        "nhi_employer",
    ):
        assert r[key] == 0.0


def test_other_earnings_flow_into_gross_and_deductions():
    base = _run(3000.0)
    with_bonus = _run(3000.0, other_earnings=500.0)
    assert with_bonus["gross"] == pytest.approx(base["gross"] + 500.0, abs=0.01)
    assert with_bonus["ssb_employee"] >= base["ssb_employee"]


def test_other_deduction_reduces_net_directly():
    r_no_ded = _run(3000.0)
    r_with_ded = _run(3000.0, other_deduction=100.0)
    assert r_no_ded["net"] - r_with_ded["net"] == pytest.approx(100.0, abs=0.01)


def test_partial_period_prorates_gross():
    full = _run(3000.0, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28))
    partial = _run(
        3000.0,
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
        employee_start_date=date(2026, 2, 15),
    )
    assert partial["gross"] < full["gross"]
    assert partial["worked_days"] < partial["period_days"]


def test_calc_nhi_matches_real_celery_payslips():
    """NHI's 7.50%-total, split-evenly-3.75%/3.75% formula reconciled to
    the cent against two real Celery-issued BVI payslips for two different
    employees at two different employers. Both bases are well under the
    106,800/year (8,900/month) wage ceiling, so this doesn't exercise the
    cap — only the split.

    Employee 1: base = gross 2,693.11 + sickness 302.61 + taxed fringe
    200.00 = 3,195.72; payslip shows employer NHI 119.84, EE NHI 119.84.

    Employee 2: base = gross 3,717.25 + taxed car allowance 100.00 +
    sickness 287.18 = 4,104.43; payslip shows employer NHI 153.92, EE NHI
    153.91 (one-cent rounding on the payslip itself).
    """
    rates = NHIRates(employer_pct=3.75, employee_pct=3.75, wage_ceiling_monthly=8900.0)

    employee_1 = calc_nhi(3195.72, rates)
    assert employee_1["employer"] == pytest.approx(119.84, abs=0.01)
    assert employee_1["employee"] == pytest.approx(119.84, abs=0.01)

    employee_2 = calc_nhi(4104.43, rates)
    assert employee_2["employer"] == pytest.approx(153.92, abs=0.01)
    assert employee_2["employee"] == pytest.approx(153.91, abs=0.01)


def test_calc_ssb_age_65_switch_matches_real_celery_payslip():
    """A real Celery-issued BVI payslip for a 66-year-old (DOB confirmed
    on the slip as April 15, 1959; pay period ends January 31, 2026) shows
    Social Security going employer-only at a flat 0.50% — exactly the rate
    sheet's "Age limit for switch to SSB: 65 years" / "SSB percentage:
    0.50%" fields — with employee contribution dropping to 0.00%, on a
    gross of 7,000.00 capped at the confirmed 4,450.00 SSB ceiling
    (22.25 / 0.5% = 4,450.00 exactly — the first real example to actually
    exceed the cap).
    """
    assert age_as_of(date(1959, 4, 15), date(2026, 1, 31)) == 66

    rates = SSBRates(
        employer_pct=4.5,
        employee_pct=4.0,
        insurable_wage_ceiling_monthly=4450.0,
        switch_age=65,
        post_switch_employer_pct=0.5,
    )

    switched = calc_ssb(7000.0, rates, employee_age=66)
    assert switched["switched_to_ssb"] is True
    assert switched["insurable_wage"] == pytest.approx(4450.0, abs=0.01)
    assert switched["employer"] == pytest.approx(22.25, abs=0.01)
    assert switched["employee"] == 0.0

    # Same rates, under the switch age — falls back to the normal split,
    # proving the switch is age-gated rather than always-on.
    not_switched = calc_ssb(7000.0, rates, employee_age=64)
    assert not_switched["switched_to_ssb"] is False
    assert not_switched["employer"] == pytest.approx(4450.0 * 0.045, abs=0.01)
    assert not_switched["employee"] == pytest.approx(4450.0 * 0.04, abs=0.01)


def test_calc_ssb_full_time_and_part_time_match_real_celery_payslips():
    """Two more real Celery-issued BVI payslips (Demo BVI, both under age
    65) confirm the full-time vs part-time SSB schedule split is keyed off
    the HR App Employees track's ``employment_type`` field, not job title:

    Deemed Melfor (employment_type full_time, base 2,500.00) → full-time
    schedule: employer 112.50, employee 100.00 (8.50% total).

    Tom Hooi (employment_type part_time, base 4,104.43) → part-time
    schedule: employer 164.18, employee 143.65 (7.50% total) — confirming
    part_time_employer_pct/part_time_employee_pct, not the full-time rates
    that would otherwise apply (4.50%/4.00% would give 184.70/164.18,
    which is NOT what the payslip shows).
    """
    rates = SSBRates(
        employer_pct=4.5,
        employee_pct=4.0,
        insurable_wage_ceiling_monthly=4450.0,
        switch_age=65,
        post_switch_employer_pct=0.5,
        part_time_employer_pct=4.0,
        part_time_employee_pct=3.5,
    )

    full_time = calc_ssb(2500.0, rates, employee_age=50, employment_type="full_time")
    assert full_time["part_time_schedule"] is False
    assert full_time["employer"] == pytest.approx(112.50, abs=0.01)
    assert full_time["employee"] == pytest.approx(100.00, abs=0.01)

    part_time = calc_ssb(4104.43, rates, employee_age=29, employment_type="part_time")
    assert part_time["part_time_schedule"] is True
    assert part_time["employer"] == pytest.approx(164.18, abs=0.01)
    assert part_time["employee"] == pytest.approx(143.65, abs=0.01)
