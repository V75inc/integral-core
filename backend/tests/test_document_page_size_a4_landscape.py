"""A4 landscape is a page size like the others (payslips are A4 landscape)."""

from __future__ import annotations

import pytest

from app.services.documents.document_theme import (
    PAGE_SIZE_INCHES,
    build_document_css,
    normalize_page_size,
)

pytestmark = pytest.mark.smoke


def test_a4_landscape_is_known_and_wide():
    assert normalize_page_size("a4_landscape") == "a4_landscape"
    assert normalize_page_size("A4-Landscape") == "a4_landscape"
    w, h = PAGE_SIZE_INCHES["a4_landscape"]
    assert (w, h) == PAGE_SIZE_INCHES["a4"][::-1]


def test_a4_landscape_page_rule():
    css = build_document_css(page_size="a4_landscape")
    assert "@page { size: 11.69in 8.27in;" in css


def test_other_sizes_unchanged():
    assert normalize_page_size("letter_landscape") == "letter_landscape"
    assert normalize_page_size("a4") == "a4"
    assert normalize_page_size("nonsense") == "letter"
