"""Materialize authorized Integral skills as standard Agent Skills documents."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable, Mapping, Sequence

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
    skills: Iterable[tuple[str, str, str]],
    *,
    root: Path,
    allowed_tools: Mapping[str, Sequence[str]] | None = None,
) -> frozenset[str]:
    """Write private per-run SKILL.md copies with only standard frontmatter.

    The harness's Skills capability reads only these generated files. Source
    directories are not exposed to Pydantic AI, and the generated frontmatter
    contains only standard name, description and allowed-tools fields. Caller
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
        metadata = {"name": name, "description": clean_description}
        declared_tools = (allowed_tools or {}).get(original_name, ())
        if declared_tools:
            metadata["allowed-tools"] = " ".join(declared_tools)
        frontmatter = yaml.safe_dump(
            metadata,
            allow_unicode=True,
            sort_keys=False,
        )
        skill_file.write_text(
            f"---\n{frontmatter}---\n\n{body.strip()}\n", encoding="utf-8"
        )
        skill_file.chmod(0o600)
        selected.add(name)
    return frozenset(selected)


async def routing_skill_candidates(
    *, workspace_id, principal_id, focused_app_id, query
):
    """Advisory public metadata only; do not prepare/load operational skill bodies."""
    import asyncio

    from app.agentive.harness.capability_search import (
        _rank,
        _searchable_skill_description,
    )
    from app.agentive.services.agent_skills import list_core_skills
    from app.agentive.services.skill_registry import get_callable_skills
    from app.agentive.workspace_agent_profile import _app_slug
    from app.models.nodes import App

    core = await asyncio.to_thread(list_core_skills)
    descriptions = {
        str(row["key"]): str(row.get("description") or "")
        for row in core
        if row.get("key")
    }
    # The current resolver filters App access and private/disabled skills. Do not
    # cache its decisions or consult the filesystem/App body composition path.
    skills = await get_callable_skills(
        "", workspace_id, user_id=principal_id, active_apps_only=True
    )
    apps = {}
    for skill in skills:
        if getattr(skill, "kind", "declarative") != "declarative":
            continue
        app_id = str(getattr(skill, "app_id", "") or "")
        if app_id not in apps:
            apps[app_id] = await App.get(app_id) if app_id else None
        if apps[app_id] is None:
            continue
        key = f"{_app_slug(apps[app_id])}__{skill.key}"
        descriptions[key] = str(getattr(skill, "description", "") or "")
    ranked = _rank(
        query,
        [
            (key, _searchable_skill_description(value))
            for key, value in descriptions.items()
        ],
    )
    return [
        {"id": key, "description": " ".join(descriptions[key].split())[:500]}
        for key, _score in ranked[:8]
    ]
