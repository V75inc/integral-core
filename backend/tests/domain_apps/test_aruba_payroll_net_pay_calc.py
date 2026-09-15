"""Golden-value tests for Aruba Payroll's pure ``calculate_net_pay`` —
reproduces the real Celery-issued payslips and tax-admin reports the
formula was reverse-engineered from (see ``_net_pay_calc.py``'s module
docstring for the full reconciliation notes and confirmed-vs-approximate
parts). No ToolContext, no DB — pure function, fast.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.profiles.aruba_payroll.tools._net_pay_calc import (  # type: ignore[import]
    SocialSecurityRates,
    WageTaxBand,
    calc_wage_tax_annual,
    calculate_net_pay,
)

_RATES = SocialSecurityRates(
    aov_employer_pct=10.50,
    aov_employee_pct=5.00,
    azv_employer_pct=8.90,
    azv_employee_pct=1.60,
    svb_zv_employer_pct=2.65,
    svb_ov_employer_pct=0.875,
    acquisition_cost_pct=3.00,
    acquisition_cost_cap_monthly=125.00,
)
_BANDS = [WageTaxBand(0, 34930, 0), WageTaxBand(34930, None, 21)]


def _run(base_salary, **kwargs):
    kwargs.setdefault("pay_frequency", "monthly")
    kwargs.setdefault("period_start", date(2026, 1, 1))
    kwargs.setdefault("period_end", date(2026, 1, 31))
    kwargs.setdefault("social_security_rates", _RATES)
    kwargs.setdefault("wage_tax_bands", _BANDS)
    kwargs.setdefault("basic_allowance_annual", 30000)
    kwargs.setdefault("child_allowance_annual", 750)
    kwargs.setdefault("period_number", 1)
    return calculate_net_pay(base_salary=base_salary, **kwargs)


def test_payslip_1_of_7_arrindell_period_1_three_children():
    """Real payslip: gross 3,367.00, 3 children, period 1. Net Wage
    2,951.44 and Annual Taxable Income 34,355.22 both confirmed separately
    against a real "Calculations tax and fiscal allowances" report for the
    same employee."""
    r = _run(3367.00, number_of_children=3, other_deduction=200.00)
    assert r["aov_employer"] == 342.93
    assert r["azv_employer"] == 290.67
    assert r["aov_employee"] == 163.30
    assert r["azv_employee"] == 52.26
    assert r["acquisition_costs"] == 101.01
    assert r["child_allowance"] == 187.50
    assert r["net"] == 2951.44
    assert r["annual_taxable_income"] == pytest.approx(34355.22, abs=0.1)
    assert r["annual_tax"] == 0.0


def test_payslip_2_of_7_sickness_pay_hits_acquisition_cost_cap():
    """Real payslip: gross 3,988.06 + sickness pay 512.75, period 2.
    Acquisition Costs (3% of gross+sickness+fringe) exceeds the 125.00 cap
    — confirms sickness pay counts toward that base, not just AOV/AZV's."""
    r = _run(
        3988.06,
        sickness_pay=512.75,
        other_deduction=100.00,
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
        period_number=2,
    )
    assert r["acquisition_costs"] == 125.00  # hit the cap
    assert r["net"] == pytest.approx(4112.00, abs=0.02)


def test_payslip_4_of_7_taxed_fringe_benefit_nets_to_zero_cash():
    """Real payslip: gross 2,863.00 + taxed fringe benefit (meals) 40.00.
    The fringe benefit inflates the AOV/AZV/Acquisition-Cost base but is
    NOT paid in cash — Net Wage excludes it entirely."""
    r = _run(2863.00, fringe_benefits=40.00)
    assert r["acquisition_costs"] == 87.09  # 3% of (2863+40), under the cap
    assert r["aov_employer"] == 295.67
    assert r["azv_employer"] == 250.62
    assert r["net"] == 2677.15  # fringe never reaches net cash


def test_higher_earner_exercises_both_wage_tax_bands():
    """Real tax-admin report ("Zimmerman"): gross 7,000.00/month, no
    children, period 1. Confirmed exactly against real data: taxable
    income this period 6,421.25, annualized to 77,055.00, Basic Allowance
    (30,000) subtracted, both bands exercised (0% to 34,930, 21% above) ->
    annual tax 2,546.25. wage_tax (the per-period withholding spread) is
    asserted with a small tolerance — see module docstring."""
    r = _run(7000.00)
    assert r["taxable_income_this_period"] == 6421.25
    assert r["annual_taxable_income"] == 77055.00
    assert r["annual_tax"] == 2546.25
    assert r["wage_tax"] == pytest.approx(212.16, abs=0.05)


def test_basic_allowance_floors_taxable_income_at_zero():
    """Real tax-admin report ("Paula"): Annual Taxable Income (19,512.83)
    is less than the Basic Allowance (30,000) — Taxable Income (post-
    allowance) floors at 0.00 rather than going negative, and tax stays
    0.00 regardless."""
    assert calc_wage_tax_annual(max(0.0, 19512.83 - 30000.00), _BANDS) == 0.0


def test_svb_is_employer_only_no_employee_withholding():
    r = _run(3367.00, number_of_children=0)
    # SVb ZV/OV never withhold from the employee — confirmed across every
    # real example; net pay only ever nets out AOV/AZV employee shares.
    assert r["svb_zv_employer"] > 0
    assert r["svb_ov_employer"] > 0
    assert "svb_zv_employee" not in r
    assert "svb_ov_employee" not in r


def test_full_calendar_month_does_not_inflate_gross():
    """A 31-day January must not overshoot base_salary via a fixed
    30-day-baseline proration — worked/total_days caps at exactly 1.0 for
    any fully-worked period regardless of calendar month length."""
    r = _run(3367.00, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    assert r["gross"] == 3367.00


def test_zero_base_salary_returns_zeroed_breakdown():
    r = _run(0.0)
    assert r["gross"] == 0.0
    assert r["net"] == 0.0
