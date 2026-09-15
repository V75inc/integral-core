"""``compute_fold`` must match across accents, or uniqueness silently splits.

Until jvspatial text normalization was disabled, the storage layer ASCII-folded
every persisted string: a workspace named "Café" was stored as "Cafe" and the
fold column -- a plain casefold -- held "cafe". With normalization off the same
input persists as "Café", so a casefold-only helper produces "café", which does
not match the "cafe" already stored on every row written before the change.

Uniqueness keys off these columns (see services/uniqueness.py), so the two stop
colliding and a duplicate that looks identical in the UI can be created. Folding
the accents here keeps the match key stable across that change.
"""

from __future__ import annotations

import pytest

from app.api.validators_common import compute_fold

pytestmark = pytest.mark.smoke


def test_accented_and_plain_share_a_fold_key():
    """The regression: these two must collide, or duplicates slip through."""
    assert compute_fold("Café") == compute_fold("Cafe")
    assert compute_fold("Café") == "cafe"


def test_still_case_and_whitespace_insensitive():
    """The behaviour that already existed must survive the change."""
    assert compute_fold("  Ideas  ") == compute_fold("ideas")
    assert compute_fold("PRODUCT PIPELINE") == compute_fold("Product Pipeline")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Zoë", "zoe"),
        ("naïve", "naive"),
        ("Ünicode", "unicode"),
        ("São Paulo", "sao paulo"),
    ],
)
def test_strips_combining_marks(value: str, expected: str):
    assert compute_fold(value) == expected


def test_empty_and_none_are_stable():
    assert compute_fold(None) == ""
    assert compute_fold("") == ""
    assert compute_fold("   ") == ""


def test_non_latin_text_is_preserved_not_replaced():
    """Folding must not degrade to the storage layer's old "?" replacement.

    jvspatial's normalizer replaced anything it could not map with "?", which
    collapsed distinct names onto the same key. Two different Greek words must
    still fold apart.
    """
    a = compute_fold("Ελλάδα")
    b = compute_fold("Αθήνα")
    assert a != b
    assert "?" not in a and "?" not in b
