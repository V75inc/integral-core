"""Tests for ``_payslip_pdf.render_payslip_pdf`` — the actual PDF drawing.
Renders a real PDF via pymupdf and asserts on its extracted text layer
(cheap, deterministic — catches the exact bug hit while building this: a
too-tight ``insert_textbox`` rect silently drops the text instead of
raising, so a naive "did it run without throwing" test would pass on a
payslip missing every dollar amount).
"""

from __future__ import annotations

from datetime import date

import pymupdf
import pytest

from app.profiles.payroll_app.tools._payslip_pdf import (
    render_payslip_pdf,  # type: ignore[import]
)

RESULT = {
    "gross": 950000.0,
    "nis_employee_contribution": 15680.0,
    "taxable_income": 934320.0,
    "paye_tax": 201080.0,
    "net": 733240.0,
    "worked_days": 30,
    "period_days": 30,
}


def _render(**overrides):
    kwargs = dict(
        company_name="V75 Inc.",
        company_address="1 Wren Avenue, Georgetown",
        logo_bytes=None,
        pay_run_title="2026-06 pay run",
        period_label="June 2026",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        pay_date=date(2026, 7, 1),
        employee_title="Jane Doe",
        designation="Software Developer I",
        result=RESULT,
    )
    kwargs.update(overrides)
    return render_payslip_pdf(**kwargs)


def _text(pdf_bytes: bytes) -> str:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count == 1
    return doc[0].get_text()


def test_renders_a_single_page_pdf():
    pdf_bytes = _render()
    assert pdf_bytes[:4] == b"%PDF"
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count == 1


def test_all_figures_and_identity_fields_are_present_in_the_text_layer():
    text = _text(_render())
    for expected in (
        "V75 Inc.",
        "1 Wren Avenue, Georgetown",
        "Jane Doe",
        "Software Developer I",
        "June 2026",
        "2026-06-01 to 2026-06-30",
        "950,000.00",
        "15,680.00",
        "201,080.00",
        "216,760.00",  # NIS + PAYE combined deductions total
        "733,240.00",  # net pay
        "NET PAY",
    ):
        assert expected in text, f"{expected!r} missing from rendered payslip text"


def test_missing_designation_renders_placeholder_not_blank():
    text = _text(_render(designation=""))
    assert "DESIGNATION" in text
    assert "-\n" in text or text.strip().endswith("-")


def test_no_company_address_omits_it_without_error():
    pdf_bytes = _render(company_address="")
    text = _text(pdf_bytes)
    assert "Jane Doe" in text  # still renders fine


def test_logo_bytes_embed_without_error():
    logo_doc = pymupdf.open()
    logo_page = logo_doc.new_page(width=20, height=20)
    logo_page.draw_rect(pymupdf.Rect(0, 0, 20, 20), color=(0, 0, 1), fill=(0, 0, 1))
    logo_bytes = logo_page.get_pixmap().tobytes("png")

    pdf_bytes = _render(logo_bytes=logo_bytes)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    assert len(doc[0].get_images()) == 1


def test_corrupt_logo_bytes_do_not_crash_rendering():
    pdf_bytes = _render(logo_bytes=b"not a real image")
    text = _text(pdf_bytes)
    assert "Jane Doe" in text  # falls back gracefully, still renders


def test_zero_net_pay_renders_zero_not_blank():
    zero_result = {
        **RESULT,
        "gross": 0.0,
        "nis_employee_contribution": 0.0,
        "taxable_income": 0.0,
        "paye_tax": 0.0,
        "net": 0.0,
    }
    text = _text(_render(result=zero_result))
    assert "0.00" in text
