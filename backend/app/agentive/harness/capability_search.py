"""Unified read-only search over a run's authorized Harness capabilities."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, Sequence

import yaml
from pydantic import Field

from app.agentive.harness.pydantic_ai_compat import Tool, ToolReturn

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
_RRF_K = 60
logger = logging.getLogger(__name__)


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


def _searchable_skill_description(description: str) -> str:
    """Remove negative routing clauses from positive retrieval text only."""
    clauses = re.split(r"(?<=[.!?;])\s+|(?<=[.!?;])(?=[A-Z])", description)
    positive = [
        clause.strip()
        for clause in clauses
        if clause.strip()
        and not re.search(
            r"\b(?:do not|don't|never|must not)\b", clause, flags=re.IGNORECASE
        )
    ]
    return " ".join(positive)


def _tool_workflow_context(name: str, skill: dict[str, str] | None) -> str:
    """Return sentences where the selected skill explains using a tool.

    Tool manifests often contain long routing warnings. Those warnings are
    important to the model after discovery, but counting their repeated
    vocabulary as positive search evidence ranks tools that the description
    explicitly says not to use. The authorized skill body gives a concise,
    workflow-level reference when it names a tool.
    """
    if skill is None:
        return ""
    body = skill.get("body", "")
    passages: list[str] = []
    for match in re.finditer(re.escape(name), body):
        start = (
            max(
                body.rfind(".", 0, match.start()),
                body.rfind("?", 0, match.start()),
                body.rfind("!", 0, match.start()),
            )
            + 1
        )
        boundaries = [
            position
            for marker in (".", "?", "!")
            if (position := body.find(marker, match.end())) >= 0
        ]
        end = min(boundaries) + 1 if boundaries else len(body)
        passage = " ".join(body[start:end].split())
        plain_passage = re.sub(r"[*_`]+", "", passage)
        # Skill procedures often name a tool inside a prohibition before they
        # describe its valid workflow later (for example, excluding build and
        # verify tools from a design-only turn). Such mentions are negative
        # evidence for a recommendation, even though their vocabulary overlaps
        # the request. Keep the filter scoped to the skill-authored passage;
        # user intent itself is never gated by these words.
        if re.search(
            r"\b(?:do not|don't|never|must not|forbidden)\b",
            plain_passage,
            flags=re.IGNORECASE,
        ):
            continue
        if passage and passage not in passages:
            passages.append(passage)
        if len(passages) == 3:
            break
    return " ".join(passages)


def _tool_text(
    item: dict[str, Any],
    *,
    workflow_context: str = "",
    include_schema_details: bool = False,
) -> str:
    """Build a compact searchable summary without implementation data."""
    description = " ".join(str(item.get("description") or "").split())
    # The first sentence states the tool's positive purpose. Remaining
    # sentences frequently describe alternatives and prohibited routes; those
    # remain visible in normal tool discovery but should not inflate relevance.
    summary = re.split(r"(?<=[.!?])\s+", description, maxsplit=1)[0]
    summary = re.split(r"[:;]", summary, maxsplit=1)[0]
    fields = item.get("input_schema", {}).get("properties", {})
    parameter_terms: list[str] = []
    if isinstance(fields, dict):
        for name, specification in fields.items():
            parameter_terms.append(str(name))
            if include_schema_details and isinstance(specification, dict):
                parameter_terms.append(
                    " ".join(str(specification.get("description") or "").split())
                )
                enum_values = specification.get("enum")
                if isinstance(enum_values, list):
                    parameter_terms.extend(str(value) for value in enum_values)
    return " ".join(
        (
            str(item.get("name") or ""),
            summary,
            *parameter_terms,
            workflow_context,
        )
    )


def _tool_schema_text(item: dict[str, Any]) -> str:
    """Return argument contracts as a separate searchable document field."""
    fields = item.get("input_schema", {}).get("properties", {})
    if not isinstance(fields, dict):
        return ""
    terms: list[str] = []
    for name, specification in fields.items():
        terms.append(str(name))
        if isinstance(specification, dict):
            terms.append(" ".join(str(specification.get("description") or "").split()))
            enum_values = specification.get("enum")
            if isinstance(enum_values, list):
                terms.extend(str(value) for value in enum_values)
    return " ".join(terms)


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
    ranked_skill_ids: Sequence[tuple[str, float]] | None = None,
    ranked_tool_ids: Sequence[tuple[str, float]] | None = None,
) -> dict[str, Any]:
    # Skill frontmatter is its portable routing contract. Ranking on the full
    # instruction body lets long procedural documents drown out a concise,
    # highly relevant skill description.
    skill_docs = {
        skill["name"]: " ".join(
            (skill["name"], _searchable_skill_description(skill["description"]))
        )
        for skill in skills
    }
    ranked_skills = (
        list(ranked_skill_ids)
        if ranked_skill_ids is not None
        else _rank(query, [(name, document) for name, document in skill_docs.items()])
    )
    skills_by_name = {skill["name"]: skill for skill in skills}
    # Rank tools against the user's words and the selected skill's own
    # instructions where it explicitly names a capability. Do not append the
    # skill description as query text: shared terms such as "new app" and
    # "design" make schema-only or build tools dominate even when the skill
    # routes the request to a proposal workflow.
    selected_skill = skills_by_name[ranked_skills[0][0]] if ranked_skills else None
    tool_documents: list[tuple[str, str]] = []
    for item in tools:
        name = str(item["name"])
        workflow_context = _tool_workflow_context(name, selected_skill)
        tool_documents.append(
            (
                name,
                _tool_text(item, workflow_context=workflow_context),
            )
        )
    lexical_tools = _rank(query, tool_documents)
    schema_tools = _rank(
        query,
        [
            (str(item["name"]), schema_text)
            for item in tools
            if (schema_text := _tool_schema_text(item))
        ],
    )
    if ranked_tool_ids is None:
        if selected_skill is None:
            ranked_tools = lexical_tools
        else:
            # If semantic retrieval is unavailable, keep the skill-authored
            # tool relationships as a lexical signal. This is a fallback
            # ordering, not an eligibility rule: unrelated catalog entries
            # remain searchable after the named workflow tools.
            workflow_names = {
                name
                for name, document in tool_documents
                if _tool_workflow_context(name, selected_skill)
            }
            workflow_ranked = _rank(
                query,
                [
                    (name, document)
                    for name, document in tool_documents
                    if name in workflow_names
                ],
            )
            general_ranked = _rank(
                query,
                [
                    (name, document)
                    for name, document in tool_documents
                    if name not in workflow_names
                ],
            )
            ranked_tools = workflow_ranked + general_ranked
    else:
        # Fuse independent rankings rather than raw scores: embedding cosine
        # and BM25 are not calibrated to the same scale. RRF lets exact terms
        # in parameter descriptions correct weak semantic matches while still
        # recovering paraphrases through semantic rank.
        lexical_ranks = {
            name: rank for rank, (name, _score) in enumerate(lexical_tools, 1)
        }
        schema_ranks = {
            name: rank for rank, (name, _score) in enumerate(schema_tools, 1)
        }
        semantic_ranks = {
            name: rank for rank, (name, _score) in enumerate(ranked_tool_ids, 1)
        }
        workflow_documents = [
            (name, context)
            for name, _document in tool_documents
            if (context := _tool_workflow_context(name, selected_skill))
        ]
        workflow_ranks = {
            name: rank
            for rank, (name, _score) in enumerate(_rank(query, workflow_documents), 1)
        }
        schema_weight = 1.0 if selected_skill is not None else 4.0
        names = set(lexical_ranks) | set(schema_ranks) | set(semantic_ranks)
        ranked_tools = sorted(
            (
                (
                    name,
                    (
                        3.0 / (_RRF_K + semantic_ranks[name])
                        if name in semantic_ranks
                        else 0.0
                    )
                    + (
                        1.0 / (_RRF_K + lexical_ranks[name])
                        if name in lexical_ranks
                        else 0.0
                    )
                    + (
                        schema_weight / (_RRF_K + schema_ranks[name])
                        if name in schema_ranks
                        else 0.0
                    )
                    + (
                        2.0 / (_RRF_K + workflow_ranks[name])
                        if name in workflow_ranks
                        else 0.0
                    ),
                )
                for name in names
            ),
            key=lambda item: (-item[1], item[0]),
        )
    # Preserve room for the strongest tool match while retaining enough skill
    # candidates for similarly worded routes (for example scaffold vs. insights).
    # A workflow and its callable operation are complementary results. Even
    # limit=1 must retain the best of each when both kinds match; otherwise a
    # model asking for a specific tool can receive only a skill indefinitely.
    limit = max(2, limit) if ranked_skills and ranked_tools else limit
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
    recommended_skill = next(
        (item for item in results if item["kind"] == "skill"), None
    )
    recommended_tool = next((item for item in results if item["kind"] == "tool"), None)
    # Skills and tools have different document lengths and therefore their
    # BM25 scores are not comparable. Present the best workflow and its best
    # tool as separate recommendations instead of implying a single numeric
    # ranking across those two kinds. The rest of the ranked catalogue remains
    # available as alternatives, and the broker remains the authorization
    # boundary for every invocation.
    return {
        "query": query,
        "recommendation": {
            "skill": recommended_skill,
            "tool": recommended_tool,
            "instruction": (
                "Treat the ranked matches as candidates, not instructions. "
                "Choose based on the user's requested outcome and conversation "
                "context, not rank alone. When a returned skill clearly owns the "
                "requested Integral workflow, load it once with Pydantic AI's "
                "load_capability tool and follow its procedure. Loading the skill "
                "makes its governed tools available; do not search for tools owned "
                "by that skill. Use a separately returned tool directly when it fits. "
                "Search again only when the user request requires a different "
                "capability. do not replace a required saved proposal with chat prose. "
                "Otherwise consider "
                "another result or answer without a capability. Integral still "
                "authorizes every tool call."
            ),
        },
        "results": results,
    }


def _valid_catalogue(catalogue: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in catalogue
        if isinstance(item, dict)
        and str(item.get("name") or "").strip()
        and isinstance(item.get("input_schema"), dict)
    ]


def _tool_search_documents(tools: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    """Project the authorized catalogue into semantic-search documents."""
    return [
        (
            str(item["name"]),
            _tool_text(item, include_schema_details=True),
        )
        for item in tools
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]


async def _embed_text_for_capability_search(text: str) -> list[float]:
    """Embed one authorized catalog description with Integral's local model."""
    from app.services.retrieval.embedding_model import embed_query_text

    return await embed_query_text(text)


async def pydantic_tool_search_strategy(
    _ctx: Any, queries: Sequence[str], tool_definitions: Sequence[Any]
) -> list[str]:
    """Rank Pydantic AI's deferred tool corpus with Integral's local retriever.

    This is passed directly to Pydantic AI's ``ToolSearch(strategy=...)``. The
    model remains responsible for deciding whether it needs a tool; this
    callback only ranks the authorized deferred definitions Pydantic supplied.
    Reciprocal-rank fusion combines semantic similarity with BM25 so exact
    product vocabulary remains useful without acting as a hard eligibility
    gate. If embeddings are unavailable, lexical ranking is a degraded-search
    fallback, never an authorization decision.
    """
    query = " ".join(str(value).strip() for value in queries if str(value).strip())
    definitions = [
        item
        for item in tool_definitions
        if isinstance(getattr(item, "name", None), str)
        and getattr(item, "name", "").strip()
    ]
    if not query or not definitions:
        return []

    documents: list[tuple[str, str]] = []
    for item in definitions:
        name = str(item.name)
        description = " ".join(str(getattr(item, "description", "") or "").split())
        schema = getattr(item, "parameters_json_schema", None)
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        argument_names = list(properties) if isinstance(properties, dict) else []
        documents.append((name, " ".join((name, description, *argument_names))))

    lexical = _rank(query, documents)
    try:
        vectors = await asyncio.gather(
            _embed_text_for_capability_search(query),
            *(_embed_text_for_capability_search(text) for _, text in documents),
        )
        query_vector, *document_vectors = vectors
        query_norm = math.sqrt(sum(value * value for value in query_vector)) or 1.0
        semantic = []
        for (name, _document), vector in zip(documents, document_vectors):
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            similarity = sum(
                left * right for left, right in zip(query_vector, vector)
            ) / (query_norm * norm)
            semantic.append((name, similarity))
        semantic.sort(key=lambda item: (-item[1], item[0]))
    except Exception:
        logger.warning(
            "Local semantic Pydantic tool search failed; using lexical ranking",
            exc_info=True,
        )
        return [name for name, _score in lexical[:8]]

    lexical_ranks = {name: rank for rank, (name, _score) in enumerate(lexical, 1)}
    semantic_ranks = {name: rank for rank, (name, _score) in enumerate(semantic, 1)}
    fused = sorted(
        (
            (
                name,
                1.2 / (60 + lexical_ranks.get(name, len(documents) + 1))
                + 1.0 / (60 + semantic_ranks[name]),
            )
            for name, _score in semantic
        ),
        key=lambda item: (-item[1], item[0]),
    )
    return [name for name, _score in fused[:8]]


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
    skill_owned_tools: frozenset[str] = frozenset(),
) -> Tool[Any, Any]:
    """Build a model-callable search surface over one run's authorized catalog."""
    skills = _load_skills(skill_library)
    tools = _valid_catalogue(catalogue)
    cached_results: dict[tuple[str, int], dict[str, Any]] = {}
    tool_documents = _tool_search_documents(tools)
    tool_vectors: dict[str, list[float]] = {}
    state = run_state if run_state is not None else {}
    state.setdefault("capability_search_completed", False)

    async def rank_skills_semantically(query: str) -> list[tuple[str, float]]:
        """Rank authorized skill descriptions by local semantic similarity.

        The embedding model is Integral's existing local retrieval model. This
        avoids an unmetered auxiliary LLM call and does not send the tenant's
        skill catalog to another provider. Ranking is advisory; Skills and the
        live broker remain the Pydantic/Core loading and authorization paths.
        """
        if not skills:
            return []
        texts = [
            " ".join(
                (
                    skill["name"],
                    _searchable_skill_description(skill["description"]),
                )
            )
            for skill in skills
        ]
        lexical = _rank(
            query,
            [
                (
                    skill["name"],
                    " ".join(
                        (
                            skill["name"],
                            _searchable_skill_description(skill["description"]),
                        )
                    ),
                )
                for skill in skills
            ],
        )
        vectors = await asyncio.gather(
            _embed_text_for_capability_search(query),
            *(_embed_text_for_capability_search(text) for text in texts),
        )
        query_vector, *skill_vectors = vectors
        query_norm = math.sqrt(sum(value * value for value in query_vector)) or 1.0
        semantic = []
        for skill, vector in zip(skills, skill_vectors):
            vector_norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            similarity = sum(
                left * right for left, right in zip(query_vector, vector)
            ) / (query_norm * vector_norm)
            semantic.append((skill["name"], similarity))
        semantic.sort(key=lambda item: (-item[1], item[0]))
        lexical_ranks = {name: rank for rank, (name, _score) in enumerate(lexical, 1)}
        semantic_ranks = {name: rank for rank, (name, _score) in enumerate(semantic, 1)}
        # Reciprocal-rank fusion uses independent lexical and semantic signals.
        # Semantic intent leads; lexical overlap remains a correction signal so
        # record-level terms do not drown out a structurally different workflow.
        return sorted(
            (
                (
                    name,
                    1.0 / (60 + lexical_ranks.get(name, len(skills) + 1))
                    + 1.2 / (60 + semantic_ranks[name]),
                )
                for name, _score in semantic
            ),
            key=lambda item: (-item[1], item[0]),
        )

    async def rank_tools_semantically(query: str) -> list[tuple[str, float]]:
        """Rank authorized tools by meaning and cache static catalog vectors."""
        if not tool_documents:
            return []
        missing = [
            (name, document)
            for name, document in tool_documents
            if document not in tool_vectors
        ]
        query_vector, *document_vectors = await asyncio.gather(
            _embed_text_for_capability_search(query),
            *(
                _embed_text_for_capability_search(document)
                for _name, document in missing
            ),
        )
        for (_name, document), vector in zip(missing, document_vectors):
            tool_vectors[document] = vector
        query_norm = math.sqrt(sum(value * value for value in query_vector)) or 1.0
        semantic = []
        for name, document in tool_documents:
            vector = tool_vectors[document]
            vector_norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            similarity = sum(
                left * right for left, right in zip(query_vector, vector)
            ) / (query_norm * vector_norm)
            # A zero or negative cosine is not a semantic match. Keeping such
            # entries in the ranking makes arbitrary identifier tie-breaking
            # vote in reciprocal-rank fusion and can promote unrelated tools.
            if similarity > 0:
                semantic.append((name, similarity))
        semantic.sort(key=lambda item: (-item[1], item[0]))
        return semantic

    async def search_capabilities(
        query: Annotated[str, Field(min_length=2, max_length=_MAX_QUERY_CHARS)],
        limit: Annotated[int, Field(ge=1, le=_MAX_RESULTS)] = 8,
    ) -> ToolReturn:
        """Find the most relevant authorized Integral skill and tool metadata."""
        normalized_query = " ".join(query.lower().split())
        cache_key = (normalized_query, limit)
        if cache_key in cached_results:
            result = cached_results[cache_key]
            return ToolReturn(return_value=result, tools=result["available_tools"])
        state["capability_search_completed"] = True
        try:
            ranked_skills = await rank_skills_semantically(query)
        except Exception:
            # Keep discovery operational on deployments where local retrieval
            # embeddings are disabled or unavailable. Tool/skill invocation
            # remains governed by Pydantic AI and the Integral broker.
            logger.warning(
                "Local semantic capability ranking failed; using lexical ranking",
                exc_info=True,
            )
            ranked_skills = None
        try:
            ranked_tools = await rank_tools_semantically(query)
        except Exception:
            logger.warning(
                "Local semantic tool ranking failed; using lexical ranking",
                exc_info=True,
            )
            ranked_tools = None
        result = _search_catalog(
            query=query,
            limit=limit,
            skills=skills,
            tools=tools,
            immediately_available_tools=immediately_available_tools,
            ranked_skill_ids=ranked_skills,
            ranked_tool_ids=ranked_tools,
        )
        result["ranking_method"] = (
            "hybrid_semantic_lexical"
            if ranked_skills or ranked_tools is not None
            else "lexical"
        )
        state["capability_ranking_method"] = result["ranking_method"]
        recommendation = result.get("recommendation", {})
        recommended_skill = recommendation.get("skill")
        recommended_tool = recommendation.get("tool")
        state["recommended_skill_id"] = (
            recommended_skill.get("name")
            if isinstance(recommended_skill, dict)
            else None
        )
        state["recommended_tool_id"] = (
            recommended_tool.get("name") if isinstance(recommended_tool, dict) else None
        )
        available_tools = [
            item["name"]
            for item in result["results"]
            if item["kind"] == "tool" and item["name"] not in skill_owned_tools
        ]
        for item in result["results"]:
            if item["kind"] == "tool":
                item.pop("discover_with", None)
        result["available_tools"] = available_tools
        result["recommendation"]["instruction"] += (
            " The returned tool schemas are disclosed by this search. Call the "
            "selected tool directly when available; no second tool search is "
            "needed. Search again only if observed results require a different "
            "capability."
        )
        cached_results[cache_key] = result
        return ToolReturn(return_value=result, tools=available_tools)

    return Tool(
        search_capabilities,
        name="search_capabilities",
        description=(
            "Search the authorized Integral Agent Skills and tool catalog for "
            "the best capabilities for a user request. Start with "
            "recommendation.skill and recommendation.tool when they fit. Load a "
            "matched skill with Pydantic AI's load_capability tool; the framework "
            "then makes that skill's governed tools available. This search reveals "
            "separately matched tool schemas directly. Other results are "
            "alternatives. "
            "Search guides discovery but does not authorize or execute a tool."
        ),
        takes_ctx=False,
        defer_loading=False,
    )
