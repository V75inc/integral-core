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


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_BUILD_INTENT_RE = re.compile(
    r"\b(build|create|set\s+up|setup|stand\s+up|scaffold|new\s+app|"
    r"confirm\s+(?:this|the)\s+design)\b",
    re.IGNORECASE,
)
_BUILD_VERIFY_RE = re.compile(r"\b(?:verify|reverify|recheck)\b.*\bbuild\b", re.I)
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "for",
        "from",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "we",
        "with",
        "you",
        "your",
    }
)


def select_relevant_skill_sources(
    skills: Iterable[tuple[str, str, str]], *, user_text: str, limit: int = 3
) -> tuple[tuple[str, str, str], ...]:
    """Select a small, deterministic Agent Skills set for this user turn.

    Core skill libraries are intentionally broad. Making every skill available
    to every model turn encourages repeated deferred-capability loads and
    bloats prompts. Greenfield build and explicit design-confirmation turns
    have one clear owner: ``integral-scaffold``. Other turns use lexical
    relevance over standard skill names and descriptions, with a small cap.
    This affects guidance discovery only; the Core tool broker remains the
    authority for callable tools and every effect.
    """
    candidates = tuple(
        item
        for item in skills
        if len(item) == 3 and str(item[0]).strip() and str(item[2]).strip()
    )
    if not candidates:
        return ()

    by_name = {str(item[0]).strip().lower(): item for item in candidates}
    if _BUILD_INTENT_RE.search(user_text):
        scaffold = by_name.get("integral-scaffold")
        if scaffold is not None:
            return (scaffold,)

    query = {
        token
        for token in _TOKEN_RE.findall(user_text.lower())
        if token not in _STOP_WORDS and len(token) > 2
    }
    if not query:
        return ()

    scored: list[tuple[int, str, tuple[str, str, str]]] = []
    for item in candidates:
        name, description, _ = item
        skill_terms = {
            token
            for token in _TOKEN_RE.findall(f"{name} {description}".lower())
            if token not in _STOP_WORDS and len(token) > 2
        }
        score = len(query & skill_terms)
        if score:
            scored.append((score, str(name).lower(), item))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(item[2] for item in scored[: max(0, limit)])


def split_eager_skill_sources(
    skills: Iterable[tuple[str, str, str]], *, user_text: str
) -> tuple[tuple[tuple[str, str, str], ...], tuple[tuple[str, str, str], ...]]:
    """Inline lifecycle-critical skills and leave other skills deferred.

    Pydantic AI's deferred capability loader treats repeated loads as errors.
    Models sometimes repeat a harmless load after tool-heavy turns, and the
    resulting failed ``load_capability`` call can leave a durable effect
    unresolved. Integral's scaffold workflow is the one skill whose ownership
    must persist across proposal and confirmation turns, so supply that
    permission-filtered document once as instructions. Other relevant skills
    retain the Harness-native deferred Skills surface.
    """
    selected = tuple(skills)
    if not _BUILD_INTENT_RE.search(user_text):
        return (), selected
    eager = tuple(item for item in selected if item[0].strip() == "integral-scaffold")
    deferred = tuple(item for item in selected if item not in eager)
    return eager, deferred


def scaffold_workflow_tools(
    *, user_text: str, has_pending_design: bool, affirmative: bool = False
) -> frozenset[str] | None:
    """Return direct tool visibility for an active scaffold lifecycle.

    ``None`` leaves the normal workspace tool discovery policy unchanged.
    Proposal and build tools otherwise remain deferred in the large catalogue,
    which let a model answer in prose without creating a saved proposal or
    reach the single-call approved builder. Confirmation only exposes the
    builder when Core already has a saved design on this conversation.
    """
    # The tool itself validates the durable design and chat approval. Make an
    # exact confirmation route to that authority even when the thread snapshot
    # supplied to the provider has not hydrated its marker yet; otherwise an
    # approved build turn falls back to proposal tools and can narrate work it
    # cannot perform.
    if affirmative:
        return frozenset({"integral_build_approved_design", "integral_verify_build"})
    if has_pending_design and _BUILD_VERIFY_RE.search(user_text):
        return frozenset({"integral_verify_build"})
    if not has_pending_design and not _BUILD_INTENT_RE.search(user_text):
        return None
    tools = {"integral_propose_design", "integral_list_apps"}
    return frozenset(tools)


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
