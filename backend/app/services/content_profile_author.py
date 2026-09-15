"""v1 deterministic NL → manifest template-fill (Phase 6 Plan 06-04 — MCP-04).

F0: keyword routing uses catalog listing metadata (``package.tags`` +
``package.name``) from seeded library ContentProfiles — not a hardcoded
substrate ``DOMAIN_KEYWORD_MAP``. Pass ``package_slug`` to skip NL matching.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _tokenize(text: str) -> set:
    """Lowercase + non-alphanumeric split — used for keyword-overlap scoring."""
    return {tok for tok in _TOKEN_SPLIT_RE.split(text.lower()) if tok}


def _as_list(raw: Any) -> List[Any]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return [raw]


async def _load_library_rows() -> List[Any]:
    from app.models.nodes import ContentProfile

    raw = await ContentProfile.find({"context.library_package": True})
    return _as_list(raw)


def _package_match_tokens(cp: Any) -> set:
    """Tokens from package.name, metadata.slug, and package.tags / metadata."""
    tokens: set = set()
    name = str(getattr(cp, "name", "") or "")
    tokens |= _tokenize(name)
    md = dict(getattr(cp, "metadata", None) or {})
    slug = str(md.get("slug") or "")
    if slug:
        tokens |= _tokenize(slug.replace("-", " "))
    manifest = dict(getattr(cp, "manifest", None) or {})
    pkg = dict(manifest.get("package") or {})
    for tag in pkg.get("tags") or md.get("tags") or []:
        tokens |= _tokenize(str(tag))
    # Also score package.description lightly.
    desc = str(pkg.get("description") or getattr(cp, "description", "") or "")
    tokens |= {t for t in _tokenize(desc) if len(t) > 3}
    return tokens


async def author_profile_from_template(
    *,
    description: str,
    target_scope: str,
    fields: Optional[List[Dict[str, Any]]] = None,
    package_slug: Optional[str] = None,
) -> Dict[str, Any]:
    """v1 deterministic NL → manifest (template-fill, no LLM).

    Algorithm:

    1. If ``package_slug`` is set, load that library package by metadata.slug.
    2. Else tokenize description and score each library package by tag/name
       overlap; highest score wins (ties broken by catalog order).
    3. If a package matched, return its manifest (optionally merging ``fields``).
    4. If no match, emit a generic ``post``-style entry-type.

    Returns the manifest dict (NOT persisted).
    """
    rows = await _load_library_rows()
    best: Optional[Any] = None

    if package_slug:
        slug = package_slug.strip()
        for cp in rows:
            md = dict(getattr(cp, "metadata", None) or {})
            if str(md.get("slug") or "") == slug:
                best = cp
                break
    else:
        desc_tokens = _tokenize(description)
        best_score = 0
        for cp in rows:
            score = len(_package_match_tokens(cp) & desc_tokens)
            if score > best_score:
                best_score = score
                best = cp

    if best is not None:
        manifest = dict(best.manifest or {})
        if fields:
            tier = manifest.get("track")
            if tier is None:
                app_tracks = (manifest.get("app") or {}).get("tracks") or []
                if app_tracks:
                    tier = app_tracks[0]
            if isinstance(tier, dict):
                ets = tier.get("entry_types") or []
                if ets:
                    ets[0].setdefault("fields", []).extend(fields)
        return manifest

    return {
        "content_profile_schema_version": 2,
        "scope": target_scope,
        "package": {
            "name": "ad-hoc",
            "description": description[:200],
        },
        "track": {
            "entry_types": [
                {
                    "key": "post",
                    "name": "Post",
                    "fields": fields
                    or [{"key": "body", "name": "Body", "type": "markdown"}],
                }
            ],
        },
    }


# Deprecated alias — kept so older imports do not break; always empty.
DOMAIN_KEYWORD_MAP: List = []

__all__ = ["DOMAIN_KEYWORD_MAP", "author_profile_from_template"]
