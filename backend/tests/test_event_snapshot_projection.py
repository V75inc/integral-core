"""Snapshot inspection is bounded and recipient projection fails closed."""

import pytest

from app.services.event_snapshot_projection import snapshot_references

pytestmark = pytest.mark.smoke


def test_detects_nested_references_in_keys_values_and_text():
    assert snapshot_references({"n.Entry.one": ["See n.Track.two for context"]}) == {
        ("entry", "n.Entry.one"),
        ("track", "n.Track.two"),
    }


@pytest.mark.parametrize(
    "value",
    [
        {"secret": "n.Unknown.private"},
        "x" * 65537,
        list(range(1001)),
        [f"n.Entry.{index}" for index in range(101)],
    ],
)
def test_incomplete_or_undeclared_inspection_withholds_block(value):
    assert snapshot_references(value) is None


def test_aggregate_text_budget_withholds_many_individually_small_strings():
    assert snapshot_references(["x" * 60000] * 3) is None


def test_deep_snapshot_withheld():
    value = {"id": "n.Entry.one"}
    for _ in range(21):
        value = {"nested": value}
    assert snapshot_references(value) is None


def test_declared_content_is_still_bounded_and_only_root_strings_are_exempt():
    assert (
        snapshot_references(
            {"text": "n.Entry.private"}, content_fields=frozenset({"text"})
        )
        == set()
    )
    assert (
        snapshot_references({"text": "x" * 65537}, content_fields=frozenset({"text"}))
        is None
    )
    assert snapshot_references(
        {"nested": {"text": "n.Entry.private"}}, content_fields=frozenset({"text"})
    ) == {("entry", "n.Entry.private")}
    assert snapshot_references(
        {"text": {"id": "n.Entry.private"}}, content_fields=frozenset({"text"})
    ) == {("entry", "n.Entry.private")}
