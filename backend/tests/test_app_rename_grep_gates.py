"""Grep-based completeness gates for the Phase 10 / Plan 10-01 Space → App rename.

These tests assert the substrate-level halves of APP-RENAME-01:

1. ``class Space`` no longer exists in ``backend/app/models/nodes.py``.
2. ``class App(Node)`` exists with ``__entity_name__ == "App"`` — the
   user-facing primitive (formerly Space).
3. ``class IntegralApp(Node)`` exists with ``__entity_name__ == "IntegralApp"`` —
   the renamed root singleton (formerly App). The discriminator string is
   unchanged from the pre-rename state.
4. ``class Apps(Node)`` exists with ``__entity_name__ == "Apps"`` — the
   per-workspace registry (formerly Spaces).

The test suite at the end of Plan 10-01 is intentionally red across the broader
``backend/`` codebase (api/spaces.py and friends still reference the now-removed
``Space`` import); Plan 10-02 in the same PR closes that gap. These gates focus
narrowly on the Node-class layer that Plan 10-01 owns.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# Resolve nodes.py relative to this test file so the test is portable across
# the developer machine / CI / worktree layouts.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_NODES_PY = _REPO_ROOT / "backend" / "app" / "models" / "nodes.py"


def test_no_class_space_in_models() -> None:
    """``class Space`` is gone from ``backend/app/models/nodes.py``."""
    assert _NODES_PY.exists(), f"nodes.py missing at {_NODES_PY}"
    # ``grep -c '^class Space\b'`` returns count. We want count == 0.
    result = subprocess.run(
        ["grep", "-cE", r"^class Space\b", str(_NODES_PY)],
        capture_output=True,
        text=True,
    )
    # grep exits 1 when there are 0 matches; either way, count must be 0.
    count = int(result.stdout.strip() or "0")
    assert count == 0, (
        f"Found {count} ``class Space`` definitions in nodes.py — "
        "Phase 10 Plan 10-01 requires the Space class be renamed to App."
    )


def test_class_app_exists_with_correct_discriminator() -> None:
    """``App`` is importable and ``App.__entity_name__ == "WorkspaceApp"``.

    The discriminator is "WorkspaceApp" (not "App") to avoid collision with
    jvagent's own ``App`` node class. DB rows were migrated Space → App →
    WorkspaceApp. The discriminator must NOT be changed to "App" without a
    DB migration.
    """
    from app.models.nodes import App  # noqa: WPS433 (local import is intentional)

    assert App.__entity_name__ == "WorkspaceApp", (
        f"App.__entity_name__ == {App.__entity_name__!r}; "
        'expected "WorkspaceApp" — discriminator collision avoidance with jvagent '
        "(see nodes.py App class docstring). Do not change without a DB migration."
    )


def test_singleton_renamed_to_IntegralApp() -> None:
    """``IntegralApp`` is importable; discriminator string unchanged."""
    from app.models.nodes import IntegralApp  # noqa: WPS433

    assert IntegralApp.__entity_name__ == "IntegralApp", (
        f"IntegralApp.__entity_name__ == {IntegralApp.__entity_name__!r}; "
        'expected "IntegralApp" (the original singleton discriminator, '
        "unchanged across the Step A rename)."
    )


def test_spaces_registry_renamed_to_Apps() -> None:
    """``Apps`` is importable; discriminator string == "Apps"."""
    from app.models.nodes import Apps  # noqa: WPS433

    assert Apps.__entity_name__ == "Apps", (
        f"Apps.__entity_name__ == {Apps.__entity_name__!r}; " 'expected "Apps".'
    )


def test_old_space_class_no_longer_importable() -> None:
    """``from app.models.nodes import Space`` raises ImportError.

    Hard cutover per ``migrations/space_to_app_rename.md`` — no
    backwards-compat alias is retained.
    """
    with pytest.raises(ImportError):
        from app.models.nodes import Space  # noqa: F401,WPS433


def test_old_spaces_registry_no_longer_importable() -> None:
    """``from app.models.nodes import Spaces`` raises ImportError."""
    with pytest.raises(ImportError):
        from app.models.nodes import Spaces  # noqa: F401,WPS433
