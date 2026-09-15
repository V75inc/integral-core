"""Resolve @ / # tokens in chat messages for agent context (no notifications)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.nodes import App, Track, User
from app.schemas.chat_entity_refs import (
    EntityRef,
    EntityRefResolutionResult,
    ResolvedEntityRef,
)
from app.services.filing_resolution import resolve_space_by_name, resolve_track_by_name
from app.services.mentions import extract_mention_tokens, resolve_mention_token
from app.services.permissions import (
    get_user_accessible_apps,
    get_user_accessible_tracks,
)
from app.utils.text_matching import casefold_match

_HASH_RX = re.compile(r"#([A-Za-z0-9_.\-]+)")


def extract_hash_tokens(text: str) -> List[str]:
    """Extract unique #slug tokens from text, preserving order."""
    if not text:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for m in _HASH_RX.findall(text):
        if m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


async def _load_user(user_id: str) -> Optional[User]:
    try:
        return await User.get(user_id)
    except Exception:
        return None


async def _load_app(app_id: str) -> Optional[App]:
    try:
        return await App.get(app_id)
    except Exception:
        return None


async def _load_track(track_id: str) -> Optional[Track]:
    try:
        return await Track.get(track_id)
    except Exception:
        return None


def _workspace_matches(node_ws: str, workspace_id: Optional[str]) -> bool:
    if not workspace_id:
        return True
    return (node_ws or "") == workspace_id


async def _validate_explicit_ref(
    ref: EntityRef,
    user_id: str,
    workspace_id: Optional[str],
) -> Optional[ResolvedEntityRef]:
    if ref.kind == "user":
        user = await _load_user(ref.id)
        if not user:
            return None
        return ResolvedEntityRef(
            kind="user",
            entity_id=user.id,
            label=ref.label,
            display_name=getattr(user, "display_name", None) or ref.display_name,
            email=getattr(user, "email", None) or ref.email,
        )
    if ref.kind == "app":
        app = await _load_app(ref.id)
        if not app:
            return None
        accessible = await get_user_accessible_apps(user_id)
        if not any(a.id == app.id for a in accessible):
            return None
        if not _workspace_matches(getattr(app, "workspace_id", "") or "", workspace_id):
            return None
        return ResolvedEntityRef(
            kind="app",
            entity_id=app.id,
            label=ref.label or (app.name or app.id),
            subtitle=ref.subtitle,
        )
    if ref.kind == "track":
        track = await _load_track(ref.id)
        if not track:
            return None
        accessible = await get_user_accessible_tracks(user_id)
        if not any(t.id == track.id for t in accessible):
            return None
        if not _workspace_matches(
            getattr(track, "workspace_id", "") or "", workspace_id
        ):
            return None
        return ResolvedEntityRef(
            kind="track",
            entity_id=track.id,
            label=ref.label or (track.title or track.id),
            subtitle=ref.subtitle,
        )
    return None


async def _resolve_hash_token(
    token: str,
    user_id: str,
    workspace_id: Optional[str],
) -> Tuple[List[ResolvedEntityRef], List[dict]]:
    """Resolve a single #token; may return multiple candidates when ambiguous."""
    apps = await get_user_accessible_apps(user_id)
    if workspace_id:
        apps = [
            a
            for a in apps
            if _workspace_matches(getattr(a, "workspace_id", "") or "", workspace_id)
        ]
    tracks = await get_user_accessible_tracks(user_id)
    if workspace_id:
        tracks = [
            t
            for t in tracks
            if _workspace_matches(getattr(t, "workspace_id", "") or "", workspace_id)
        ]

    app_hits: List[Tuple[App, float]] = []
    for app in apps:
        name = app.name or ""
        score = casefold_match(
            token, (getattr(app, "name_fold", None) or name.casefold())
        )
        if score >= 0.5:
            app_hits.append((app, score))

    track_hits: List[Tuple[Track, float]] = []
    for track in tracks:
        title = track.title or ""
        score = casefold_match(
            token, (getattr(track, "title_fold", None) or title.casefold())
        )
        if score >= 0.5:
            track_hits.append((track, score))

    # Also try filing_resolution helpers for best single match
    space_match = await resolve_space_by_name(user_id, token, min_score=0.5)
    track_match = await resolve_track_by_name(user_id, token, min_score=0.5)
    if space_match:
        app, score = space_match
        if workspace_id and not _workspace_matches(
            getattr(app, "workspace_id", "") or "", workspace_id
        ):
            pass
        elif not any(a.id == app.id for a, _ in app_hits):
            app_hits.append((app, score))
    if track_match:
        track, score = track_match
        if workspace_id and not _workspace_matches(
            getattr(track, "workspace_id", "") or "", workspace_id
        ):
            pass
        elif not any(t.id == track.id for t, _ in track_hits):
            track_hits.append((track, score))

    all_hits: List[Tuple[str, Any, float]] = []
    for app, score in app_hits:
        all_hits.append(("app", app, score))
    for track, score in track_hits:
        all_hits.append(("track", track, score))

    if not all_hits:
        return [], []

    all_hits.sort(key=lambda x: x[2], reverse=True)
    top_score = all_hits[0][2]
    top_tier = [h for h in all_hits if h[2] >= top_score - 0.05]

    if len(top_tier) > 1:
        ambiguous = []
        for kind, node, score in top_tier[:5]:
            label = (
                getattr(node, "name", None) or getattr(node, "title", None) or node.id
            )
            ambiguous.append(
                {
                    "token": token,
                    "kind": kind,
                    "entity_id": node.id,
                    "label": label,
                    "score": score,
                }
            )
        return [], ambiguous

    kind, node, _ = top_tier[0]
    label = getattr(node, "name", None) or getattr(node, "title", None) or node.id
    return [
        ResolvedEntityRef(
            kind=kind,  # type: ignore[arg-type]
            entity_id=node.id,
            label=label,
        )
    ], []


async def _resolve_mention_token_to_ref(token: str) -> Optional[ResolvedEntityRef]:
    user = await resolve_mention_token(token)
    if not user:
        return None
    return ResolvedEntityRef(
        kind="user",
        entity_id=user.id,
        label=token,
        display_name=getattr(user, "display_name", None),
        email=getattr(user, "email", None),
    )


def _format_preamble(resolved: List[ResolvedEntityRef]) -> str:
    if not resolved:
        return ""
    lines = ["Referenced entities (user-selected or resolved from message):"]
    for r in resolved:
        if r.kind == "user":
            extra = ""
            if r.display_name:
                extra = f' "{r.display_name}"'
            if r.email:
                extra += f" <{r.email}>"
            lines.append(f"- User{extra} (id={r.entity_id})")
        elif r.kind == "app":
            lines.append(f'- App "{r.label}" (id={r.entity_id})')
        else:
            lines.append(f'- Track "{r.label}" (id={r.entity_id})')
    return "\n".join(lines)


def entities_referenced_payload(
    resolved: List[ResolvedEntityRef],
) -> List[Dict[str, Any]]:
    """Serialize resolved entity refs into the wire payload for agent context injection."""
    type_map = {"user": "User", "app": "App", "track": "Track"}
    out: List[Dict[str, Any]] = []
    for r in resolved:
        out.append(
            {
                "entity_type": type_map[r.kind],
                "entity_id": r.entity_id,
                "label": r.label,
            }
        )
    return out


async def resolve_entity_refs(
    text: str,
    explicit_refs: Optional[List[EntityRef]],
    user_id: str,
    workspace_id: Optional[str] = None,
) -> EntityRefResolutionResult:
    """Merge explicit picker refs with best-effort @ / # parsing from text."""
    resolved_by_id: Dict[Tuple[str, str], ResolvedEntityRef] = {}
    ambiguous: List[dict] = []

    for raw in explicit_refs or []:
        validated = await _validate_explicit_ref(raw, user_id, workspace_id)
        if validated:
            resolved_by_id[(validated.kind, validated.entity_id)] = validated

    explicit_mention_tokens: Set[str] = set()
    explicit_hash_tokens: Set[str] = set()
    for raw in explicit_refs or []:
        label_fold = (raw.label or "").strip().casefold()
        if raw.kind == "user":
            if label_fold:
                explicit_mention_tokens.add(label_fold)
            explicit_mention_tokens.add(raw.id.casefold())
        else:
            if label_fold:
                explicit_hash_tokens.add(label_fold)
            explicit_hash_tokens.add(raw.id.casefold())

    for token in extract_mention_tokens(text):
        if token.lower() in explicit_mention_tokens:
            continue
        ref = await _resolve_mention_token_to_ref(token)
        if ref:
            resolved_by_id[(ref.kind, ref.entity_id)] = ref

    for token in extract_hash_tokens(text):
        if token.lower() in explicit_hash_tokens:
            continue
        refs, amb = await _resolve_hash_token(token, user_id, workspace_id)
        ambiguous.extend(amb)
        for ref in refs:
            resolved_by_id[(ref.kind, ref.entity_id)] = ref

    resolved = list(resolved_by_id.values())
    preamble = _format_preamble(resolved)
    if ambiguous:
        preamble += "\n\nAmbiguous references (ask the user to clarify):\n" + "\n".join(
            f"- #{a['token']}: {a['kind']} \"{a['label']}\" (id={a['entity_id']})"
            for a in ambiguous
        )
    return EntityRefResolutionResult(
        resolved=resolved,
        ambiguous=ambiguous,
        context_preamble=preamble,
    )


def focused_ids_from_resolved(
    resolved: List[ResolvedEntityRef],
) -> Tuple[Optional[str], Optional[str]]:
    """Derive focused_track_id / focused_space_id when exactly one of each kind."""
    tracks = [r for r in resolved if r.kind == "track"]
    apps = [r for r in resolved if r.kind == "app"]
    focused_track = tracks[0].entity_id if len(tracks) == 1 else None
    focused_space = apps[0].entity_id if len(apps) == 1 else None
    return focused_track, focused_space
