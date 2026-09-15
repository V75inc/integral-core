"""Regression coverage for the legacy SSB/NHI/Payroll-Tax formula's output
contract with ``pay_run_line_calc.py``.

``recalc_pay_run_line`` falls back to ``calculate_net_pay`` only when no
``svb_premium_rates`` page is configured, so this path is easy to leave
untested and unexercised — which is exactly what let it ship silently
unreachable (dead code) AND internally broken (it referenced field names
``calculate_net_pay`` never returns, e.g. ``aov_employee``) at the same
time. This test locks in the actual output-key contract so a future
refactor of either side breaks loudly instead of silently.
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

MODULE = (
    Path(__file__).parents[2] / "app/profiles/curacao-payroll/tools/_net_pay_calc.py"
)
SPEC = importlib.util.spec_from_file_location("curacao_net_pay_calc", MODULE)
assert SPEC and SPEC.loader
calc = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = calc
SPEC.loader.exec_module(calc)


def _rates():
    ssb_rates = calc.SSBRates(
        employer_pct=4.5, employee_pct=4.0, insurable_wage_ceiling_monthly=4450.0
    )
    nhi_rates = calc.NHIRates(
        employer_pct=3.75, employee_pct=3.75, wage_ceiling_monthly=8900.0
    )
    payroll_tax_class = calc.PayrollTaxClass(
        employer_class="small",
        employer_rate_pct=2.0,
        employee_rate_pct=8.0,
        remuneration_threshold_annual=10000.0,
        small_employer_exemption_threshold_annual=150000.0,
    )
    return ssb_rates, nhi_rates, payroll_tax_class


def test_calculate_net_pay_returns_the_keys_pay_run_line_calc_consumes() -> None:
    """pay_run_line_calc.py's legacy-fallback branch reads gross/basic/net
    plus each *_employee/*_employer key off this result, then derives
    total_deductions/employer_cost itself (calculate_net_pay has no
    aggregate notion of either). If any of these keys is ever renamed here
    without updating that branch, it fails with a KeyError the moment the
    fallback is exercised — same failure mode this test is guarding."""
    ssb_rates, nhi_rates, payroll_tax_class = _rates()
    result = calc.calculate_net_pay(
        base_salary=5000,
        pay_frequency="monthly",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        ssb_rates=ssb_rates,
        payroll_tax_class=payroll_tax_class,
        nhi_rates=nhi_rates,
    )
    for key in (
        "gross",
        "basic",
        "ssb_employee",
        "ssb_employer",
        "payroll_tax_employee",
        "payroll_tax_employer",
        "nhi_employee",
        "nhi_employer",
        "other_deduction",
        "net",
    ):
        assert (
            key in result
        ), f"calculate_net_pay dropped {key!r} — pay_run_line_calc.py's fallback reads it directly"

    # Aggregate fields pay_run_line_calc.py derives itself (not part of
    # this pure calculator's own return shape) — with no other_deduction,
    # total_deductions must exactly bridge gross down to net.
    total_deductions = round(
        result["ssb_employee"]
        + result["payroll_tax_employee"]
        + result["nhi_employee"]
        + result["other_deduction"],
        2,
    )
    employer_cost = round(
        result["gross"]
        + result["ssb_employer"]
        + result["payroll_tax_employer"]
        + result["nhi_employer"],
        2,
    )
    assert total_deductions == round(result["gross"] - result["net"], 2)
    assert employer_cost > result["gross"]


def test_net_equals_gross_minus_the_three_employee_deductions() -> None:
    """Net must reconcile to gross minus SSB/payroll-tax/NHI employee shares
    minus other_deduction — no silent extra deduction or rounding drift."""
    ssb_rates, nhi_rates, payroll_tax_class = _rates()
    result = calc.calculate_net_pay(
        base_salary=5000,
        pay_frequency="monthly",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        ssb_rates=ssb_rates,
        payroll_tax_class=payroll_tax_class,
        nhi_rates=nhi_rates,
        other_deduction=25,
    )
    expected_net = round(
        result["gross"]
        - result["ssb_employee"]
        - result["payroll_tax_employee"]
        - result["nhi_employee"]
        - result["other_deduction"],
        2,
    )
    assert result["net"] == expected_net
