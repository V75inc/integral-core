"""Server side of the list controls: how a sort is read, and which fields a pick-list may be asked about."""

import pytest

from app.api.errors import BadRequestError
from app.api.tracks import _parse_sort_param
from app.services.entry_facets import _PATH, _value_of


def test_sort_is_a_field_or_minus_field():
    assert _parse_sort_param(None) is None
    assert _parse_sort_param("  ") is None
    assert _parse_sort_param("title") == [("title", 1)]
    assert _parse_sort_param("-custom_fields.gross") == [("custom_fields.gross", -1)]


@pytest.mark.parametrize("bad", ["custom_fields.gross; drop", "-", "1abc", "a b", "x" * 200, "$where"])
def test_a_malformed_sort_is_refused(bad):
    with pytest.raises(BadRequestError):
        _parse_sort_param(bad)


@pytest.mark.parametrize("ok", ["title", "custom_fields.department", "job_title"])
def test_facet_fields_are_plain_field_names(ok):
    assert _PATH.match(ok)


@pytest.mark.parametrize("bad", ["", "context.id", "custom_fields.", "a.b.c", "x;y", "$ne", "../x"])
def test_other_field_paths_are_not_accepted(bad):
    assert not _PATH.match(bad)


def test_values_are_read_from_the_projected_row():
    row = {"context": {"custom_fields": {"department": "n.Entry.d1"}, "title": "Ann"}}
    assert _value_of(row, "context.custom_fields.department") == "n.Entry.d1"
    assert _value_of(row, "context.title") == "Ann"
    assert _value_of(row, "context.custom_fields.missing") is None
