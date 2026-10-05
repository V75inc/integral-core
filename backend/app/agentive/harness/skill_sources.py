"""Materialize authorized Integral skills as standard Agent Skills documents."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable

import yaml


def _portable_name(value: str) -> str:
    """Convert an Integral skill key into the portable Agent Skills name form."""
    name = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not name:
        name = "integral-skill"
    if len(name) > 64:
        suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        name = f"{name[:55].rstrip('-')}-{suffix}"
    return name


def materialize_standard_skill_library(
    skills: Iterable[tuple[str, str, str]], *, root: Path
) -> frozenset[str]:
    """Write private per-run SKILL.md copies with only standard frontmatter.

    The harness's Skills capability reads only these generated files. Source
    directories are not exposed to Pydantic AI, and the generated frontmatter
    contains only the standard ``name`` and ``description`` fields. Caller
    supplies an already authorization-filtered skill set.
    """
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    selected: set[str] = set()
    for original_name, description, body in skills:
        name = _portable_name(original_name)
        if name in selected:
            suffix = hashlib.sha256(original_name.encode("utf-8")).hexdigest()[:8]
            name = f"{name[:55].rstrip('-')}-{suffix}"
        if name in selected:
            raise ValueError("authorized skill names are not uniquely addressable")
        clean_description = " ".join(str(description or "").split())[:1024]
        if not clean_description:
            raise ValueError(f"skill {original_name!r} has no description")
        folder = root / name
        folder.mkdir(mode=0o700)
        skill_file = folder / "SKILL.md"
        frontmatter = yaml.safe_dump(
            {"name": name, "description": clean_description},
            allow_unicode=True,
            sort_keys=False,
        )
        skill_file.write_text(
            f"---\n{frontmatter}---\n\n{body.strip()}\n", encoding="utf-8"
        )
        skill_file.chmod(0o600)
        selected.add(name)
    return frozenset(selected)
