"""Locate the native resident's portable skill assets in source or a wheel."""

from __future__ import annotations

from pathlib import Path


def resolve_resident_agent_root(start: Path) -> Path:
    """Find portable skills bundled with the source checkout or installed wheel."""
    for parent in start.parents:
        checkout = parent / "agent"
        if (checkout / "skills").is_dir() and (
            parent / "backend/pyproject.toml"
        ).is_file():
            return checkout
    for parent in start.parents:
        packaged = parent / "resident_harness"
        if parent.name == "app" and (packaged / "skills").is_dir():
            return packaged
    raise RuntimeError("Integral AI resident skills are missing from this installation")


def resident_agent_root() -> Path:
    """Return this installation's native resident asset root."""
    return resolve_resident_agent_root(Path(__file__).resolve())
