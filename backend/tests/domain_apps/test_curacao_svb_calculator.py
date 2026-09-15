"""Regression coverage for Curaçao SVB premiums and wage-tax seeds."""

import asyncio
import importlib.util
import sys
import types
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import yaml

MODULE = (
    Path(__file__).parents[2] / "app/profiles/curacao-payroll/tools/_svb_calculator.py"
)
SPEC = importlib.util.spec_from_file_location("curacao_svb_calculator", MODULE)
assert SPEC and SPEC.loader
calc = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = calc
SPEC.loader.exec_module(calc)

WAGE_TAX_MODULE = (
    Path(__file__).parents[2] / "app/profiles/curacao-payroll/tools/_wage_tax_table.py"
)
WAGE_TAX_SPEC = importlib.util.spec_from_file_location(
    "curacao_wage_tax_table", WAGE_TAX_MODULE
)
assert WAGE_TAX_SPEC and WAGE_TAX_SPEC.loader
wage_tax = importlib.util.module_from_spec(WAGE_TAX_SPEC)
sys.modules[WAGE_TAX_SPEC.name] = wage_tax
WAGE_TAX_SPEC.loader.exec_module(wage_tax)

EXPORT_PACKAGE = "curacao_export_test"
export_package = types.ModuleType(EXPORT_PACKAGE)
export_package.__path__ = [str(MODULE.parent)]
sys.modules[EXPORT_PACKAGE] = export_package
EXPORT_SPEC = importlib.util.spec_from_file_location(
    f"{EXPORT_PACKAGE}.statutory_documents", MODULE.parent / "statutory_documents.py"
)
assert EXPORT_SPEC and EXPORT_SPEC.loader
exports = importlib.util.module_from_spec(EXPORT_SPEC)
sys.modules[EXPORT_SPEC.name] = exports
EXPORT_SPEC.loader.exec_module(exports)

RATE_SPEC = importlib.util.spec_from_file_location(
    f"{EXPORT_PACKAGE}._statutory_rates", MODULE.parent / "_statutory_rates.py"
)
assert RATE_SPEC and RATE_SPEC.loader
statutory_rates = importlib.util.module_from_spec(RATE_SPEC)
sys.modules[RATE_SPEC.name] = statutory_rates
RATE_SPEC.loader.exec_module(statutory_rates)


def test_2026_monthly_premiums_use_published_splits() -> None:
    """Published monthly SVB shares calculate to the expected cents."""
    premiums = calc.calculate_svb_premiums(
        gross_wage=Decimal("5000"),
        periods_per_year=12,
        rates=calc.rates_2026(ov_employer_pct=1),
    )
    assert premiums["aov_employee"] == Decimal("300.00")
    assert premiums["aov_employer"] == Decimal("450.00")
    assert premiums["aww_employee"] == Decimal("25.00")
    assert premiums["bvz_employee"] == Decimal("215.00")
    assert premiums["avbz_employee"] == Decimal("75.00")
    assert premiums["zv_wage_base"] == Decimal("5000.00")
    assert premiums["ov_wage_base"] == Decimal("5000.00")
    assert premiums["employee_total"] == Decimal("615.00")


def test_2026_monthly_ceilings_are_applied() -> None:
    """Published SVB ceilings cap the applicable premium bases."""
    premiums = calc.calculate_svb_premiums(
        gross_wage=Decimal("20000"),
        periods_per_year=12,
        rates=calc.rates_2026(ov_employer_pct=5),
    )
    assert premiums["aov_employee"] == Decimal("616.67")
    assert premiums["zv_employer"] == Decimal("135.78")
    assert premiums["bvz_employee"] == Decimal("537.50")


def test_2026_weekly_zv_ov_ceiling_is_converted_from_monthly() -> None:
    premiums = calc.calculate_svb_premiums(
        gross_wage=Decimal("2000"),
        periods_per_year=52,
        rates=calc.rates_2026(ov_employer_pct=1),
    )
    assert premiums["zv_employer"] == Decimal("31.33")
    assert premiums["ov_employer"] == Decimal("16.49")


def test_aov_one_percent_upper_slice_and_pension_age_exemption() -> None:
    premiums = calc.calculate_svb_premiums(
        gross_wage=Decimal("10000"),
        periods_per_year=12,
        rates=calc.rates_2026(ov_employer_pct=1),
    )
    assert premiums["aov_employee"] == Decimal("516.67")

    pensioner = calc.calculate_svb_premiums(
        gross_wage=Decimal("5000"),
        periods_per_year=12,
        rates=calc.rates_2026(ov_employer_pct=1),
        employee_age=65,
    )
    assert pensioner["aov_employee"] == Decimal("0.00")
    assert pensioner["aov_employer"] == Decimal("450.00")


def test_effective_dated_record_requires_all_premium_rates() -> None:
    """Incomplete statutory schedules fail rather than producing a partial result."""
    fields = calc.rates_2026(ov_employer_pct=1).__dict__.copy()
    fields.pop("aov_employee_pct")
    try:
        calc.rates_from_fields(fields)
    except ValueError as error:
        assert "aov_employee_pct" in str(error)
    else:
        raise AssertionError("missing statutory rate should be rejected")


def test_payroll_uses_table_derived_wage_tax_without_guessing_it() -> None:
    """Payroll totals incorporate a supplied exact-table wage-tax amount."""
    result = calc.calculate_payroll(
        gross_wage=5000,
        wage_tax_withheld=125,
        other_employee_deductions=25,
        other_employer_costs=10,
        periods_per_year=12,
        rates=calc.rates_2026(ov_employer_pct=1),
    )
    assert result["employee_deductions"] == Decimal("765.00")
    assert result["net"] == Decimal("4235.00")
    assert result["employer_cost"] == Decimal("6120.00")


def test_wage_tax_uses_exact_published_row_not_a_rate_formula() -> None:
    """Lookup selects the published discrete row at each boundary."""
    rows = [
        wage_tax.WageTaxRow(Decimal("0"), Decimal("930"), Decimal("0")),
        wage_tax.WageTaxRow(Decimal("930"), Decimal("935"), Decimal("90.68")),
        wage_tax.WageTaxRow(Decimal("935"), None, Decimal("91.16")),
    ]
    assert wage_tax.lookup_wage_tax(taxable_wage="930", rows=rows) == Decimal("90.68")
    assert wage_tax.lookup_wage_tax(taxable_wage="934.99", rows=rows) == Decimal(
        "90.68"
    )
    assert wage_tax.lookup_wage_tax(taxable_wage="935", rows=rows) == Decimal("91.16")


def test_profile_seeds_complete_official_2026_monthly_wage_tax_table() -> None:
    """The install must contain the complete Tax Office table, not a
    zero-tax default or an incomplete hand-entered subset."""
    profile_path = (
        Path(__file__).parents[2] / "app/profiles/curacao-payroll/profile.yaml"
    )
    profile = yaml.safe_load(profile_path.read_text())
    rows = [
        entry["custom_fields"]
        for group in profile["app"]["seeds"]
        if group["track"] == "settings"
        for entry in group["entries"]
        if entry["entry_type"] == "wage_tax_table_row"
    ]
    rows.sort(key=lambda row: Decimal(str(row["from_amount"])))

    assert len(rows) == 3335
    assert rows[0]["from_amount"] == 0.0
    assert rows[0]["withholding"] == 0.0
    assert rows[186]["from_amount"] == 930.0
    assert rows[186]["withholding"] == 90.68
    assert rows[-1]["from_amount"] == 16670.0
    assert rows[-1]["to_amount"] is None
    assert rows[-1]["withholding"] == 4862.91
    assert {row["pay_frequency"] for row in rows} == {"monthly"}
    assert {row["effective_date"] for row in rows} == {"2026-01-01"}
    assert all(
        Decimal(str(row["to_amount"])) == Decimal(str(next_row["from_amount"]))
        for row, next_row in zip(rows, rows[1:])
    )


def test_profile_seeds_exact_official_non_monthly_table_sets() -> None:
    profile_path = (
        Path(__file__).parents[2] / "app/profiles/curacao-payroll/profile.yaml"
    )
    profile = yaml.safe_load(profile_path.read_text())
    sets = [
        entry
        for group in profile["app"]["seeds"]
        if group["track"] == "settings"
        for entry in group["entries"]
        if entry["entry_type"] == "wage_tax_table_set"
    ]
    assert {entry["custom_fields"]["pay_frequency"] for entry in sets} == {
        "weekly",
        "biweekly",
    }

    class Context:
        async def find_entries_in_track_type(self, _track):
            return [
                SimpleNamespace(
                    custom_fields={
                        "_entry_type_slug": entry["entry_type"],
                        **entry["custom_fields"],
                    }
                )
                for entry in sets
            ]

    for frequency, expected_count in (("weekly", 771), ("biweekly", 1540)):
        rows = asyncio.run(
            statutory_rates.latest_wage_tax_rows(
                Context(), date(2026, 12, 31), frequency
            )
        )
        assert len(rows) == expected_count
        assert rows[0]["from_amount"] == 0.0
        assert rows[-1]["to_amount"] is None


def test_verzamelloonstaat_serializer_preserves_official_row_shapes() -> None:
    rows = [
        ["WG", "2026", "123456789", "", "Employer", "Address", "1", ""],
        ["WN"] + [str(index) for index in range(1, 41)],
        ["DV", "1234567890", "01-01-2026", ""],
    ]
    content = exports._verzamelloonstaat_csv_bytes(rows).decode("utf-8")
    lines = content.splitlines()
    assert [len(line.split(",")) for line in lines] == [8, 41, 4]
    assert lines[0].startswith('"WG","2026",123456789')
    assert lines[1].startswith('"WN",1,"2"')
    assert lines[2].startswith('"DV",1234567890,"01-01-2026"')


def test_verzamelloonstaat_serializer_quotes_current_spec_text_fields() -> None:
    row = [
        "WN",
        "123456789",
        "Surname",
        "Given",
        "01-01-1990",
        "1990010101",
        "Address",
        "1",
        "M",
        "OG",
        "",
        "",
        "",
        "",
        "",
        "Role",
        "5000",
        "TD",
        "0",
        "0",
        "0",
        "250",
        "J",
        "0",
        "0",
        "0",
        "N",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "100",
        "25",
        "75",
        "0",
        "0",
        "0",
        "0",
        "0",
    ]
    cells = exports._verzamelloonstaat_csv_bytes([row]).decode().strip().split(",")
    assert len(cells) == 41
    assert cells[16] == "5000"
    assert cells[17] == '"TD"'
    assert cells[22] == '"J"'
    assert cells[26] == '"N"'


def test_verzamelloonstaat_supports_child_and_contractor_record_shapes() -> None:
    rows = [
        [
            "KD",
            "1990010101",
            "Child",
            "Name",
            "01-01-2020",
            "",
            "",
            "1",
            "School",
            "I",
            "100",
        ],
        ["CO", "123456789", "Contractor", "", "", "Address", "1", "Consulting"],
        ["AR", "Contractor", "31-01-2026", "500"],
    ]
    lines = exports._verzamelloonstaat_csv_bytes(rows).decode().splitlines()
    assert [len(line.split(",")) for line in lines] == [11, 8, 4]
    assert lines[0].split(",")[10] == "100"
    assert lines[2].split(",")[3] == "500"
