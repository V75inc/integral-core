"""Unit tests for shared validation helpers (representative cases only)."""

import pytest

from app.api.validators_common import (
    compute_fold,
    non_empty_after_strip,
    validate_email,
    validate_hex_color,
    validate_semver_ish,
    validate_slug_key,
)
from app.exceptions import BadRequestError


def test_compute_fold_normalizes_unicode_and_whitespace():
    assert compute_fold("  My Workspace  ") == "my workspace"
    assert compute_fold("straße") == "strasse"
    assert compute_fold(None) == ""


@pytest.mark.parametrize(
    "value,field,match",
    [
        ("  foo  ", "name", None),
        (None, "name", "name is required"),
        ("", "name", "must not be empty"),
        ("   ", "title", "must not be empty"),
    ],
)
def test_non_empty_after_strip(value, field, match):
    if match:
        with pytest.raises(BadRequestError, match=match):
            non_empty_after_strip(value, field)
    else:
        assert non_empty_after_strip(value, field) == "foo"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("#AABBCC", "#aabbcc"),
        ("#abc", "#aabbcc"),
        ("", ""),
        (None, ""),
    ],
)
def test_validate_hex_color_accepts_canonical_forms(raw, expected):
    assert validate_hex_color(raw) == expected


def test_validate_hex_color_rejects_invalid():
    with pytest.raises(BadRequestError):
        validate_hex_color("not-hex")
    with pytest.raises(BadRequestError):
        validate_hex_color("aabbcc", allow_empty=False)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Foo@Example.COM", "foo@example.com"),
        ("  user@host.io  ", "user@host.io"),
    ],
)
def test_validate_email_normalizes(raw, expected):
    assert validate_email(raw) == expected


@pytest.mark.parametrize("bad", [None, "", "not-an-email", "user@"])
def test_validate_email_rejects_invalid(bad):
    with pytest.raises(BadRequestError):
        validate_email(bad)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.0.0", "1.0.0"),
        ("1.2", "1.2"),
        ("1.0.0-beta.1", "1.0.0-beta.1"),
        ("", ""),
        (None, ""),
    ],
)
def test_validate_semver_ish(raw, expected):
    assert validate_semver_ish(raw) == expected


def test_validate_semver_ish_rejects_garbage():
    with pytest.raises(BadRequestError):
        validate_semver_ish("garbage")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("task", "task"),
        ("task_2", "task_2"),
    ],
)
def test_validate_slug_key_accepts(raw, expected):
    assert validate_slug_key(raw) == expected


@pytest.mark.parametrize("bad", ["Task", "2task", "task-one", ""])
def test_validate_slug_key_rejects(bad):
    with pytest.raises(BadRequestError):
        validate_slug_key(bad)


def test_normalize_track_accent_color_delegates():
    from app.api.validators import normalize_track_accent_color

    assert normalize_track_accent_color("#abc") == "#aabbcc"
    with pytest.raises(BadRequestError, match="accent_color"):
        normalize_track_accent_color("not-hex")
