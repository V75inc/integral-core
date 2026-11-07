"""Core's index declarations: unchanged by default, per-kind when switched on."""

import pytest
from jvspatial.core.annotations import get_indexed_fields
from jvspatial.core.entities import Object

from app.models import indexing

pytestmark = pytest.mark.smoke


def _model(unique: bool = False):
    class Sample(Object):
        plain: str = indexing.attribute(default="", indexed=True)
        once: str = indexing.attribute(default="", indexed=True, index_unique=unique)
        unindexed: str = indexing.attribute(default="")

    return Sample


def test_default_declarations_are_unchanged(monkeypatch):
    monkeypatch.delenv("INTEGRAL_PARTIAL_INDEXES", raising=False)
    fields = get_indexed_fields(_model())
    assert "partial_by_entity" not in fields["plain"]
    assert "unindexed" not in fields


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_switch_makes_indexed_fields_per_kind(monkeypatch, value):
    monkeypatch.setenv("INTEGRAL_PARTIAL_INDEXES", value)
    fields = get_indexed_fields(_model())
    assert fields["plain"].get("partial_by_entity") is True
    assert "unindexed" not in fields


def test_unique_indexes_are_left_exactly_as_declared(monkeypatch):
    monkeypatch.setenv("INTEGRAL_PARTIAL_INDEXES", "1")
    fields = get_indexed_fields(_model(unique=True))
    assert fields["once"]["unique"] is True
    assert "partial_by_entity" not in fields["once"]


def test_an_explicit_choice_is_never_overridden(monkeypatch):
    monkeypatch.setenv("INTEGRAL_PARTIAL_INDEXES", "1")

    class Sample(Object):
        x: str = indexing.attribute(
            default="", indexed=True, index_partial_by_entity=False
        )

    assert not get_indexed_fields(Sample)["x"].get("partial_by_entity")


def test_compound_indexes_follow_the_switch(monkeypatch):
    from jvspatial.core.annotations import get_compound_indexes

    monkeypatch.setenv("INTEGRAL_PARTIAL_INDEXES", "1")

    @indexing.compound_index([("a", 1), ("b", -1)], name="idx_a_b")
    @indexing.compound_index([("a", 1)], name="idx_a_unique", unique=True)
    class Pair(Object):
        a: str = ""
        b: str = ""

    by_name = {i["name"]: i for i in get_compound_indexes(Pair)}
    assert by_name["idx_a_b"].get("partial_by_entity") is True
    assert not by_name["idx_a_unique"].get("partial_by_entity")
