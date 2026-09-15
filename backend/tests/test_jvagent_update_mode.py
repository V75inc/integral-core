"""Unit tests for the jvagent embed bootstrap update-mode env resolver.

``_jvagent_update_mode`` reads ``JVAGENT_UPDATE_MODE`` at startup and maps it
to the ``update_mode`` argument passed to ``jvagent.embed.bootstrap``. The
contract:

- default (unset) -> "source" (YAML is source of truth in dev)
- "merge" / "source" -> passed through (case-insensitive)
- "run" -> None (bootstrap's "skip existing actions" sentinel)
- invalid -> falls back to "source"
"""

import pytest

from app.main import _jvagent_update_mode


def test_default_is_source(monkeypatch):
    monkeypatch.delenv("JVAGENT_UPDATE_MODE", raising=False)
    assert _jvagent_update_mode() == "source"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("source", "source"),
        ("SOURCE", "source"),
        ("merge", "merge"),
        ("  Merge  ", "merge"),
        ("run", None),
        ("RUN", None),
    ],
)
def test_valid_values(monkeypatch, value, expected):
    monkeypatch.setenv("JVAGENT_UPDATE_MODE", value)
    assert _jvagent_update_mode() == expected


@pytest.mark.parametrize("value", ["", "bogus", "delete", "0"])
def test_invalid_falls_back_to_source(monkeypatch, value):
    monkeypatch.setenv("JVAGENT_UPDATE_MODE", value)
    assert _jvagent_update_mode() == "source"
