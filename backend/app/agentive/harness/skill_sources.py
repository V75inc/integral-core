"""Materialize authorized Integral skills as standard Agent Skills documents."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
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


def _projected_skill_names(names: Iterable[str]) -> list[str]:
    """Use the same collision handling for projection and saved references."""
    projected: list[str] = []
    selected: set[str] = set()
    for original_name in names:
        name = _portable_name(original_name)
        if name in selected:
            suffix = hashlib.sha256(original_name.encode("utf-8")).hexdigest()[:8]
            name = f"{name[:55].rstrip('-')}-{suffix}"
        occurrence = 2
        while name in selected:
            suffix = hashlib.sha256(
                f"{original_name}\0{occurrence}".encode("utf-8")
            ).hexdigest()[:8]
            name = f"{_portable_name(original_name)[:55].rstrip('-')}-{suffix}"
            occurrence += 1
        projected.append(name)
        selected.add(name)
    return projected


def project_skill_references(text: str, skill_names: Iterable[str]) -> str:
    """Annotate saved substrate skill keys with this run's native capability IDs.

    The native Skills loader has no alias facility. This is an adapter for
    Integral's persisted instructions, not a replacement loader or a grant:
    only the current permission-filtered skill library supplies this mapping.
    Preserve instruction text, including literal record values. Never guess a
    missing skill's identity or replace a quoted value with a capability ID.
    """
    names = list(skill_names)
    occurrences = Counter(names)
    projected = _projected_skill_names(names)
    # Multiple installations can expose the same substrate key. Keep both
    # native procedures discoverable, but do not guess which one an alias means.
    changed = {
        old: new
        for old, new in zip(names, projected)
        if old != new and occurrences[old] == 1
    }
    if not changed or not text:
        return text
    pattern = (
        r"(?<![\w.-])(?:"
        + "|".join(re.escape(name) for name in sorted(changed, key=len, reverse=True))
        + r")(?![\w.-])"
    )
    referenced = {match.group() for match in re.finditer(pattern, text)}
    if not referenced:
        return text
    aliases = {key: changed[key] for key in sorted(referenced)}
    return (
        text + "\n\nCurrent skill references (metadata, not additional authority): "
        "when activating a referenced workflow, use its current ID below with "
        "load_capability. These are identifier mappings only; preserve any "
        "literal record values in the instruction.\n"
        + json.dumps(aliases, ensure_ascii=False, sort_keys=True)
    )


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
    skills = list(skills)
    names = _projected_skill_names(original_name for original_name, _, _ in skills)
    for (original_name, description, body), name in zip(skills, names):
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
    return frozenset(names)


async def load_selected_workflow(
    *,
    selected_key: str | None,
    skill_sources: Sequence[tuple[str, str, str]],
    skill_directory: Path,
    allowed_skill_names: Iterable[str],
) -> tuple[str, str] | None:
    """Activate one model-selected, current procedure via the native loader.

    The normal context admission budget applies equally to initial activation
    and load_capability. A separate 16K character cutoff used to discard valid
    selections, only to load the same full body after two more model rounds.
    Never truncate a procedure or select a collision/changed body by guessing.
    """
    from app.agentive.harness.pydantic_ai_compat import LocalWorkspaceBackend, Skills

    matching = [body for key, _, body in skill_sources if key == selected_key]
    if not selected_key or len(matching) != 1:
        return None
    selected_name = next(
        name
        for (key, _, _), name in zip(
            skill_sources, _projected_skill_names(key for key, _, _ in skill_sources)
        )
        if key == selected_key
    )
    if selected_name not in allowed_skill_names:
        return None
    catalog = await Skills(
        directories=[skill_directory],
        include=[selected_name],
        workspace=LocalWorkspaceBackend(skill_directory),
    ).load()
    if len(catalog.skills) != 1:
        return None
    definition = catalog.skills[0]
    if not definition.model_invocable or definition.body != matching[0].strip():
        return None
    return definition.name, definition.render()


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
    core_keys = set(descriptions)
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
    # Native skill routing is a model decision, not a keyword intent gate.
    # Domain nouns often have zero overlap with generic Core descriptions.
    # Keep a bounded Core index visible alongside ranked App procedures so
    # the Light model can nominate the workflow without a Heavy search round.
    ranked_keys = [key for key, _score in ranked]
    core_choices = list(
        dict.fromkeys(
            [key for key in ranked_keys if key in core_keys] + sorted(core_keys)
        )
    )[:24]
    app_choices = [key for key in ranked_keys if key not in core_keys][:8]
    return [
        {"id": key, "description": " ".join(descriptions[key].split())[:500]}
        for key in [*core_choices, *app_choices]
    ]
