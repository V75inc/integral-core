"""Unified read-only search over a run's authorized Harness capabilities."""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, Sequence

import yaml
from pydantic import Field
from pydantic_ai import Tool

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP_WORDS = frozenset(
    [
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
        "the",
        "this",
        "to",
        "we",
        "with",
        "you",
        "your",
    ]
)
_MAX_QUERY_CHARS = 500
_MAX_RESULTS = 12
_BM25_K1 = 1.5
_BM25_B = 0.75


def _tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if token.lower() not in _STOP_WORDS and len(token) > 1
    ]


def _load_skills(skill_library: Path | None) -> list[dict[str, str]]:
    """Read only the already permission-filtered, projected skill library."""
    if skill_library is None or not skill_library.is_dir():
        return []
    skills: list[dict[str, str]] = []
    for skill_file in sorted(skill_library.glob("*/SKILL.md")):
        try:
            content = skill_file.read_text(encoding="utf-8")
            _opening, frontmatter, body = content.split("---", 2)
            metadata = yaml.safe_load(frontmatter) or {}
        except (OSError, ValueError, yaml.YAMLError):
            continue
        name = str(metadata.get("name") or skill_file.parent.name)
        description = " ".join(str(metadata.get("description") or "").split())
        if name and description:
            skills.append(
                {"name": name, "description": description, "body": body.strip()}
            )
    return skills


def _tool_text(item: dict[str, Any]) -> str:
    """Build a searchable summary without exposing tool implementation data."""
    fields = item.get("input_schema", {}).get("properties", {})
    parameter_terms: list[str] = []
    if isinstance(fields, dict):
        for name, schema in fields.items():
            if isinstance(schema, dict):
                parameter_terms.extend(
                    (str(name), str(schema.get("description") or ""))
                )
    return " ".join(
        (
            str(item.get("name") or ""),
            str(item.get("description") or ""),
            *parameter_terms,
        )
    )


def _excerpt(text: str, maximum: int = 320) -> str:
    normalized = " ".join(text.split())
    return (
        normalized
        if len(normalized) <= maximum
        else normalized[: maximum - 1].rstrip() + "…"
    )


def _rank(query: str, documents: Sequence[tuple[str, str]]) -> list[tuple[str, float]]:
    """Rank capability IDs with BM25; ranking never controls availability."""
    query_terms = _tokens(query)
    if not query_terms or not documents:
        return []
    tokenized = [(identifier, _tokens(document)) for identifier, document in documents]
    document_count = len(tokenized)
    average_length = sum(len(tokens) for _, tokens in tokenized) / document_count or 1.0
    document_frequency: Counter[str] = Counter()
    for _, tokens in tokenized:
        document_frequency.update(set(tokens))

    ranked: list[tuple[str, float]] = []
    for identifier, tokens in tokenized:
        frequencies = Counter(tokens)
        score = 0.0
        for term in query_terms:
            frequency = frequencies[term]
            if not frequency:
                continue
            inverse_frequency = math.log(
                1.0
                + (document_count - document_frequency[term] + 0.5)
                / (document_frequency[term] + 0.5)
            )
            denominator = frequency + _BM25_K1 * (
                1.0 - _BM25_B + _BM25_B * len(tokens) / average_length
            )
            score += inverse_frequency * (frequency * (_BM25_K1 + 1.0) / denominator)
        if score > 0:
            ranked.append((identifier, score))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def _search_catalog(
    *,
    query: str,
    limit: int,
    skills: Sequence[dict[str, str]],
    tools: Sequence[dict[str, Any]],
    immediately_available_tools: Sequence[str],
) -> dict[str, Any]:
    # Skill frontmatter is its portable routing contract. Ranking on the full
    # instruction body lets long procedural documents drown out a concise,
    # highly relevant skill description.
    skill_docs = {
        skill["name"]: " ".join((skill["name"], skill["description"]))
        for skill in skills
    }
    tool_docs = {str(item["name"]): _tool_text(item) for item in tools}
    ranked_skills = _rank(
        query, [(name, document) for name, document in skill_docs.items()]
    )
    skills_by_name = {skill["name"]: skill for skill in skills}
    tool_query = query
    if ranked_skills:
        # A selected skill's standard routing description supplies task context
        # for tool ranking, so the search can surface its workflow tools even
        # when users describe the outcome rather than the tool's API vocabulary.
        selected_skill = skills_by_name[ranked_skills[0][0]]
        tool_query = " ".join(
            (query, selected_skill["name"], selected_skill["description"])
        )
    ranked_tools = _rank(
        tool_query, [(name, document) for name, document in tool_docs.items()]
    )
    # Preserve room for the strongest tool match while retaining enough skill
    # candidates for similarly worded routes (for example scaffold vs. insights).
    skill_slots = min(len(ranked_skills), max(1, min(4, limit - 1)))
    tool_slots = min(len(ranked_tools), max(0, limit - skill_slots))
    candidates = [
        (score, "skill", name) for name, score in ranked_skills[:skill_slots]
    ] + [(score, "tool", name) for name, score in ranked_tools[:tool_slots]]
    if len(candidates) < limit:
        selected = {(kind, name) for _score, kind, name in candidates}
        remainder = [
            (score, "skill", name) for name, score in ranked_skills[skill_slots:]
        ] + [(score, "tool", name) for name, score in ranked_tools[tool_slots:]]
        remainder.sort(key=lambda item: (-item[0], item[1], item[2]))
        candidates.extend(
            item for item in remainder if (item[1], item[2]) not in selected
        )
        candidates = candidates[:limit]
    tools_by_name = {str(item["name"]): item for item in tools}
    immediately_available = frozenset(immediately_available_tools)
    results: list[dict[str, Any]] = []
    for score, kind, name in candidates[:limit]:
        if kind == "skill":
            skill = skills_by_name[name]
            results.append(
                {
                    "kind": "skill",
                    "name": name,
                    "description": _excerpt(skill["description"]),
                    "load_with": {"tool": "load_capability", "id": name},
                    "relevance": round(score, 4),
                }
            )
            continue
        item = tools_by_name[name]
        properties = item["input_schema"].get("properties", {})
        tool_result: dict[str, Any] = {
            "kind": "tool",
            "name": name,
            "description": _excerpt(str(item.get("description") or "")),
            "arguments": sorted(properties) if isinstance(properties, dict) else [],
            "relevance": round(score, 4),
        }
        if name not in immediately_available:
            tool_result["discover_with"] = {
                "tool": "search_tools",
                "queries": [name],
            }
        results.append(tool_result)
    return {"query": query, "results": results}


def _valid_catalogue(catalogue: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in catalogue
        if isinstance(item, dict)
        and str(item.get("name") or "").strip()
        and isinstance(item.get("input_schema"), dict)
    ]


def search_capabilities_for_turn(
    *,
    query: str,
    skill_library: Path | None,
    catalogue: Sequence[dict[str, Any]],
    immediately_available_tools: Sequence[str] = (),
    limit: int = 8,
) -> dict[str, Any]:
    """Run initial discovery over the catalogs authorized for this one turn."""
    return _search_catalog(
        query=query,
        limit=max(1, min(limit, _MAX_RESULTS)),
        skills=_load_skills(skill_library),
        tools=_valid_catalogue(catalogue),
        immediately_available_tools=immediately_available_tools,
    )


def build_search_capabilities_tool(
    *,
    skill_library: Path | None,
    catalogue: Sequence[dict[str, Any]],
    immediately_available_tools: Sequence[str] = (),
    run_state: dict[str, Any] | None = None,
) -> Tool[Any, Any]:
    """Build a model-callable search surface over one run's authorized catalog."""
    skills = _load_skills(skill_library)
    tools = _valid_catalogue(catalogue)
    seen_queries: set[str] = set()
    state = run_state if run_state is not None else {}
    state.setdefault("capability_search_completed", False)

    async def search_capabilities(
        query: Annotated[str, Field(min_length=2, max_length=_MAX_QUERY_CHARS)],
        limit: Annotated[int, Field(ge=1, le=_MAX_RESULTS)] = 8,
    ) -> dict[str, Any]:
        """Find the most relevant authorized Integral skill and tool metadata."""
        normalized_query = " ".join(query.lower().split())
        if normalized_query in seen_queries:
            return {
                "error": True,
                "error_code": "repeated_capability_search_suppressed",
                "message": (
                    "This query was already searched. Use the results from that "
                    "search, load the relevant skill, and call the listed tool. "
                    "If none applies, stop and explain the limitation."
                ),
                "retryable": False,
            }
        if seen_queries:
            return {
                "error": True,
                "error_code": "capability_search_limit",
                "message": (
                    "Capability discovery has already run for this turn. Use its "
                    "results to load the relevant skill and continue, or explain "
                    "which required capability was not found."
                ),
                "retryable": False,
            }
        seen_queries.add(normalized_query)
        state["capability_search_completed"] = True
        return _search_catalog(
            query=query,
            limit=limit,
            skills=skills,
            tools=tools,
            immediately_available_tools=immediately_available_tools,
        )

    return Tool(
        search_capabilities,
        name="search_capabilities",
        description=(
            "Search the authorized Integral Agent Skills and tool catalog for "
            "the best capabilities for a user request. For each skill result, "
            "load it once with load_capability. Use listed tools directly when "
            "visible, or discover deferred tool schemas with search_tools. "
            "This search does not authorize or execute a tool."
        ),
        takes_ctx=False,
        defer_loading=False,
    )
