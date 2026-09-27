"""Destination evidence for filing (W2.1).

The model still decides what the text is. This read returns inspectable
scores: schema overlap, which supplied fields land, which required fields
are missing, a small history prior, policy eligibility, and whether
embeddings were available. It does not stage an entry.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from app.services.agent_scope import accessible_tracks_for_scope, active_workspace_id
from app.services.query_boundary import generic_entry_read, parent_app_for_track
from app.services.retrieval import semantic_retrieval_available

_TOKEN = re.compile(r"[a-z0-9]+")
_NO_FIT_BELOW = 0.2
_MAX_CANDIDATES = 5


def _tokens(text: str) -> set:
    out = set()
    for token in _TOKEN.findall((text or "").casefold()):
        if len(token) < 3:
            continue
        out.add(token)
        if token.endswith("s") and len(token) > 4:
            out.add(token[:-1])
    return out


async def destination_schema(track: Any) -> List[Dict[str, Any]]:
    """Entry types and fields on a track. Empty when the track has no profile."""
    from app.services.operational_model_runtime import resolve_track_runtime_profile

    try:
        _model, tier, _key = await resolve_track_runtime_profile(track)
    except Exception:  # noqa: BLE001 — a broken profile is "no schema evidence"
        return []
    types: List[Dict[str, Any]] = []
    for raw in (tier or {}).get("entry_types") or []:
        if not isinstance(raw, dict):
            continue
        fields = []
        for field in raw.get("fields") or []:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            fields.append(
                {
                    "key": str(field["key"]),
                    "name": str(field.get("name") or field["key"]),
                    "required": bool(field.get("required")),
                }
            )
        types.append(
            {
                "key": str(raw.get("key") or raw.get("name") or "entry"),
                "name": str(raw.get("name") or raw.get("key") or "Entry"),
                "fields": fields,
            }
        )
    return types


def _score_type(
    text_tokens: set,
    entry_type: Dict[str, Any],
    supplied: Dict[str, Any],
    track_title: str,
) -> Dict[str, Any]:
    labels = _tokens(track_title) | _tokens(entry_type.get("name") or "")
    fields = list(entry_type.get("fields") or [])
    for field in fields:
        labels |= _tokens(field["name"]) | _tokens(field["key"].replace("_", " "))
    overlap = text_tokens & labels
    schema_fit = len(overlap) / max(len(labels), 1)
    known = {field["key"] for field in fields}
    mapped = {
        key: value
        for key, value in supplied.items()
        if key in known and value not in (None, "")
    }
    missing = [
        field["key"]
        for field in fields
        if field["required"] and field["key"] not in mapped
    ]
    if supplied:
        coverage = len(mapped) / max(len(fields), 1)
        score = (0.5 * schema_fit) + (0.5 * coverage)
    else:
        coverage = 0.0
        score = schema_fit
    why = []
    if overlap:
        why.append(
            f"schema overlap {len(overlap)} label(s): {', '.join(sorted(overlap))}"
        )
    if mapped:
        why.append("mapped " + ", ".join(sorted(mapped)))
    if missing:
        why.append("missing required " + ", ".join(missing))
    return {
        "entry_type": {"key": entry_type["key"], "name": entry_type["name"]},
        "schema_fit": round(schema_fit, 3),
        "field_coverage": round(coverage, 3),
        "score": score,
        "mapped_fields": mapped,
        "missing_required": missing,
        "why": why,
    }


async def _history_prior(track: Any, user_id: str) -> float:
    try:
        from app.models.edges import CONTAINS

        rows = await track.nodes(edge=[CONTAINS], node=["Entry"], limit=20)
    except Exception:  # noqa: BLE001
        return 0.0
    mine = sum(1 for row in rows if getattr(row, "author_id", "") == user_id)
    return min(mine, 5) / 5


def _facets(text: str, facets: Any) -> List[Dict[str, Any]]:
    rows = facets if isinstance(facets, list) else []
    parsed = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        supplied = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
        parsed.append(
            {
                "text": str(raw.get("text") or text or ""),
                "fields": {str(k): v for k, v in supplied.items()},
            }
        )
    if parsed:
        return parsed
    return [{"text": text or "", "fields": {}}]


async def rank_destinations(
    user_id: str,
    text: str = "",
    facets: Any = None,
) -> Dict[str, Any]:
    """Rank open tracks the caller may file into. Writes nothing."""
    workspace_id = active_workspace_id()
    pieces = _facets(text, facets)
    if not any(piece["text"].strip() or piece["fields"] for piece in pieces):
        return {
            "error": "text_required",
            "detail": "Pass the text to file, or facets that already name fields.",
        }
    tracks = await accessible_tracks_for_scope(user_id, workspace_id=workspace_id)
    open_tracks = []
    excluded = 0
    for track in tracks:
        if (await generic_entry_read(track)).allowed:
            open_tracks.append(track)
        else:
            excluded += 1
    embeddings = semantic_retrieval_available()
    semantic_reason = None if embeddings else "embeddings_unavailable"
    ranked_facets = []
    for piece in pieces:
        text_tokens = _tokens(piece["text"])
        candidates = []
        for track in open_tracks:
            types = await destination_schema(track) or [
                {"key": "entry", "name": track.title or "Entry", "fields": []}
            ]
            best = None
            for entry_type in types:
                scored = _score_type(
                    text_tokens, entry_type, piece["fields"], track.title or ""
                )
                if best is None or scored["score"] > best["score"]:
                    best = scored
            prior = await _history_prior(track, user_id)
            # History nudges a track the text already resembles. It does not
            # file an unrelated note into the busiest track.
            personalization = round(prior * 0.2, 3) if best["schema_fit"] > 0 else 0.0
            score = round(min(best["score"] + personalization, 1.0), 3)
            app = await parent_app_for_track(track)
            why = list(best["why"])
            if personalization:
                why.append("recent entries on this track raise it slightly")
            if semantic_reason:
                why.append("no embedding similarity on this deployment")
            candidates.append(
                {
                    "app_id": getattr(app, "id", None),
                    "app_name": getattr(app, "name", None),
                    "track_id": track.id,
                    "track_title": track.title,
                    "entry_type": best["entry_type"],
                    "score": score,
                    "schema_fit": best["schema_fit"],
                    "field_coverage": best["field_coverage"],
                    "personalization": personalization,
                    "semantic_similarity": None,
                    "semantic_reason": semantic_reason,
                    "policy_eligible": True,
                    "mapped_fields": best["mapped_fields"],
                    "missing_required": best["missing_required"],
                    "why": why,
                }
            )
        candidates.sort(key=lambda row: (-row["score"], row["track_title"] or ""))
        shown = candidates[:_MAX_CANDIDATES]
        best_score = shown[0]["score"] if shown else 0.0
        no_fit = round(1 - best_score, 3)
        winner = shown[0]["track_id"] if shown and best_score >= _NO_FIT_BELOW else None
        facet_why = []
        if winner is None:
            facet_why.append("no track resembles this text closely enough to file it")
        elif shown:
            facet_why.append(
                f"{shown[0]['track_title']} leads because "
                + ("; ".join(shown[0]["why"]) or "it is the closest schema")
            )
        ranked_facets.append(
            {
                "text": piece["text"],
                "no_fit": no_fit,
                "winner": winner,
                "why": facet_why,
                "candidates": shown,
            }
        )
    return {
        "facets": ranked_facets,
        "excluded_tracks": excluded,
        "note": (
            "Evidence only. You choose the destination. A high no_fit means "
            "do not file it into a track. Nothing was staged."
        ),
    }
