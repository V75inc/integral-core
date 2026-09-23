"""Locate the resident jvagent tree.

A source checkout keeps it at ``<repo>/agent``. A wheel ships a copy at
``app/resident_harness``, produced by ``.ci/bundle_resident_harness.sh``
before ``python -m build``. The checkout wins when both exist so local
edits are what boot uses.
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_resident_agent_root(start: Path) -> Path:
    """Return the agent root walking from ``start``.

    ``INTEGRAL_AGENT_ROOT`` wins when it contains ``app.yaml``. Otherwise
    a parent that holds both ``agent/app.yaml`` and ``backend/pyproject.toml``
    is the checkout. The packaged copy is the fallback.
    """
    override = os.environ.get("INTEGRAL_AGENT_ROOT", "").strip()
    if override:
        path = Path(override).expanduser()
        if (path / "app.yaml").is_file():
            return path.resolve()
    for parent in start.parents:
        checkout = parent / "agent" / "app.yaml"
        if checkout.is_file() and (parent / "backend" / "pyproject.toml").is_file():
            return checkout.parent
    for parent in start.parents:
        candidate = parent / "resident_harness" / "app.yaml"
        if parent.name == "app" and candidate.is_file():
            return candidate.parent
    return start.parents[3] / "agent" if len(start.parents) > 3 else start / "agent"


def resident_agent_root() -> Path:
    """Return the agent root for this install."""
    return resolve_resident_agent_root(Path(__file__).resolve())
