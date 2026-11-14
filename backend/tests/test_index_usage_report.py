"""The index usage report classifies and totals without touching a database."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "index_usage_report.py"
_spec = importlib.util.spec_from_file_location("index_usage_report", _PATH)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def _row(name, scans, size, definition, table="object", unique=False, primary=False):
    return report.classify(
        {
            "table_name": table,
            "index_name": name,
            "scans": scans,
            "size_bytes": size,
            "definition": definition,
            "is_unique": unique,
            "is_primary": primary,
        }
    )


def test_partial_index_names_the_kind_it_serves():
    d = "CREATE INDEX i ON public.object USING btree (x) WHERE (entity = 'DBLog'::text)"
    assert report.served_entity(d) == "DBLog"


def test_unrestricted_index_serves_any_kind():
    assert (
        report.served_entity("CREATE INDEX i ON public.object USING btree (entity, x)")
        == "any kind"
    )


def test_totals_count_unused_but_never_unique_or_primary_ones():
    rows = [
        _row("a", 0, 100, "CREATE INDEX a ON o (x)"),
        _row("b", 5, 50, "CREATE INDEX b ON o (y)"),
        _row("c", 0, 70, "CREATE UNIQUE INDEX c ON o (z)", unique=True),
        _row("d", 0, 10, "CREATE INDEX d ON o (w) WHERE (entity = 'K'::text)"),
        _row("e", 0, 5, "CREATE INDEX e ON n (q)", table="node"),
    ]
    summary = report.summarise(rows)
    assert summary["object"]["indexes"] == 4
    assert summary["object"]["unused"] == 2  # a and d; c is unique
    assert summary["object"]["unused_bytes"] == 110
    assert summary["object"]["partial"] == 1
    assert summary["node"]["unused"] == 1
    assert summary["edge"]["indexes"] == 0


def test_rendering_lists_totals_and_every_index():
    text = report.render([_row("a", 0, 2048, "CREATE INDEX a ON o (x)")])
    assert "object: 1 indexes" in text and "1 never scanned" in text and " a" in text


def test_human_sizes():
    assert report.human(512) == "512 B"
    assert report.human(5 * 1024**3) == "5.0 GB"


def test_budgets_cover_unique_indexes_without_recommending_removal():
    rows = [
        _row("a", 0, 100, "CREATE UNIQUE INDEX a ON o (x)", unique=True),
        _row("b", 10, 50, "CREATE INDEX b ON o (y)"),
    ]
    assert report.budget_violations(rows) == []
    assert report.budget_violations(rows, max_indexes=2, max_bytes=150) == []
    assert len(report.budget_violations(rows, max_indexes=1, max_bytes=149)) == 2
    assert report.budget_violations(rows, max_bytes=149)[0].startswith("object:")


def test_invalid_budgets_fail_before_database_access():
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        report.positive_int("0")
