"""Guards the one timestamp convention that `context.updated_at` may be written in.

`context.updated_at` is the first key of `DEFAULT_ENTITY_SORT`, and the keyset
cursor carries its **raw ISO string** (`pagination.encode_cursor`). Comparisons
are therefore lexicographic over whatever text the writer produced -- which only
works if every writer produces the same shape.

Two shapes were in use across `app/`:

    datetime.now().isoformat()               -> '2026-08-02T15:23:13.330842'
    datetime.now(timezone.utc).isoformat()   -> '2026-08-02T19:23:13.330849+00:00'

Those are the *same instant* on a UTC-4 host. `datetime.now()` returns **local**
time with no offset, so on any non-UTC deployment the two populations are offset
by the UTC delta and interleave wrongly under a sort that believes they are
comparable. Rows written through a naive path sort as though they happened hours
before rows written through an aware path.

`app/utils/time.py::utc_now_iso` is the single canonical writer. These tests
pin the property (offset-aware, comparable) rather than the call site, so they
keep holding as new writers are added.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.utils.time import utc_now, utc_now_iso

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke

APP_DIR = Path(__file__).resolve().parent.parent / "app"

# Writers that produce a *local-time, offset-naive* string. These are the ones
# that corrupt the sort key. `datetime.now(timezone.utc)` is offset-aware and
# therefore comparable, so it is allowed even though `utc_now_iso()` is nicer.
NAIVE_NOW = re.compile(r"datetime\.now\(\s*\)\.isoformat\(\)")
UTCNOW = re.compile(r"datetime\.utcnow\(\s*\)")


def _iter_py_files():
    for path in APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _grep(pattern: re.Pattern) -> list:
    hits = []
    for path in _iter_py_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if pattern.search(line):
                hits.append(f"{path.relative_to(APP_DIR.parent)}:{lineno}")
    return hits


class TestCanonicalHelper:
    def test_utc_now_iso_is_offset_aware(self):
        value = utc_now_iso()
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None, f"{value!r} has no offset"
        assert parsed.utcoffset() == timezone.utc.utcoffset(None)

    def test_utc_now_is_offset_aware(self):
        assert utc_now().tzinfo is not None

    def test_canonical_strings_are_lexicographically_ordered(self):
        """The property keyset pagination depends on."""
        first = utc_now_iso()
        second = utc_now_iso()
        assert first <= second
        # And string order agrees with instant order.
        assert (first <= second) == (
            datetime.fromisoformat(first) <= datetime.fromisoformat(second)
        )


class TestNaiveAndAwareAreNotComparable:
    """Demonstrates *why* the convention matters. Independent of app code."""

    def test_same_instant_renders_hours_apart_on_a_non_utc_host(self):
        naive = datetime.now().isoformat()
        aware = datetime.now(timezone.utc).isoformat()
        delta = abs(
            datetime.fromisoformat(naive)
            - datetime.fromisoformat(aware).replace(tzinfo=None)
        )
        # On a UTC host the two agree and there is nothing to prove; on any
        # other host they differ by the UTC offset while describing one moment.
        if delta.total_seconds() > 60:
            assert datetime.fromisoformat(naive).tzinfo is None
            assert datetime.fromisoformat(aware).tzinfo is not None
        else:
            pytest.skip("host is UTC; the divergence is not observable here")

    def test_mixed_shapes_break_sorting(self):
        """A naive string sorts before an aware one describing a *later* instant.

        This is exactly what `DEFAULT_ENTITY_SORT` does to rows written through
        different code paths.
        """
        earlier_aware = datetime(2026, 8, 2, 10, 0, 0, tzinfo=timezone.utc).isoformat()
        later_naive = datetime(2026, 8, 2, 11, 0, 0).isoformat()
        # The naive value describes the later instant (assuming both meant UTC),
        # yet lexicographic order is only accidentally right here...
        assert later_naive > earlier_aware
        # ...and flips as soon as the offset suffix decides the comparison:
        same_time_aware = datetime(
            2026, 8, 2, 11, 0, 0, tzinfo=timezone.utc
        ).isoformat()
        same_time_naive = datetime(2026, 8, 2, 11, 0, 0).isoformat()
        assert same_time_naive != same_time_aware
        assert (
            same_time_naive < same_time_aware
        ), "identical instants must not produce two different sort keys"


class TestNoNaiveTimestampWritersRemain:
    """Drift guard. Fails on reintroduction, with the offending file:line."""

    def test_no_naive_datetime_now_isoformat(self):
        hits = _grep(NAIVE_NOW)
        assert not hits, (
            "datetime.now().isoformat() writes a LOCAL, offset-naive string. "
            "It corrupts context.updated_at ordering on any non-UTC host. "
            "Use app.utils.time.utc_now_iso() instead.\n  " + "\n  ".join(hits)
        )

    def test_no_datetime_utcnow(self):
        hits = _grep(UTCNOW)
        assert not hits, (
            "datetime.utcnow() is deprecated (3.12+) and returns a naive "
            "datetime. Use app.utils.time.utc_now() / utc_now_iso().\n  "
            + "\n  ".join(hits)
        )
