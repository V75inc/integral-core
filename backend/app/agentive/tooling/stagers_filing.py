"""File-content fused stager: resolve hints, then stage or refuse.

``integral_file_content`` stages one entry for user approval. The agent/skill
supplies ``track_id`` or ``track_hint``, ``type_hint``, ``title``, and
``fields`` from ``integral_list_tracks`` + ``integral_get_track_schema``.
This stager performs mechanical resolution and field-key normalization only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Filing helpers — COPIED verbatim from the resident integral_tools.py.
# (Temporary duplication; the resident originals are removed in the next task.)
# --------------------------------------------------------------------------- #


def _norm_key(s: str) -> str:
    """Normalize a key for comparison: lowercase, strip non-alnum."""
    return "".join(c for c in s.lower() if c.isalnum())


_FIELD_ALIASES: Dict[str, tuple] = {
    "name": ("fullname", "fullName", "full_name", "personname", "contactname"),
    "email": ("emailaddress", "email_address", "mail", "e_mail"),
    "phone": ("phonenumber", "phone_number", "mobile", "tel", "telephone", "cell"),
    "company": ("organization", "organisation", "employer", "firm", "org"),
    "role": ("title", "jobtitle", "job_title", "position"),
    "source": ("origin", "referredby", "referred_by", "referrer"),
    "due": ("duedate", "due_date", "deadline", "by"),
    "priority": ("urgency", "importance"),
    "assignee": ("owner", "assignedto", "assigned_to"),
    "severity": ("seriousness", "level"),
    "topic": ("subject", "theme", "about"),
    "attendees": ("participants", "people", "who"),
    "date": ("when", "scheduledfor", "scheduled_for"),
}


def _normalize_agent_fields(
    agent_fields: Dict[str, Any],
    form_schema: Dict[str, Any],
) -> Dict[str, Any]:
    """Map loosely-keyed agent fields onto canonical ``form_schema`` keys."""
    if not agent_fields or not isinstance(agent_fields, dict):
        return {}
    fields_spec = form_schema.get("fields", []) if isinstance(form_schema, dict) else []
    if not fields_spec:
        return {k: v for k, v in agent_fields.items() if v is not None and v != ""}

    canonical_keys: Dict[str, str] = {}
    for f in fields_spec:
        canon = f.get("key", "")
        if not canon:
            continue
        canonical_keys[_norm_key(canon)] = canon
        name = f.get("name", "")
        if name:
            canonical_keys[_norm_key(name)] = canon
        for alias in _FIELD_ALIASES.get(_norm_key(canon), ()):
            canonical_keys.setdefault(_norm_key(alias), canon)

    out: Dict[str, Any] = {}
    for raw_key, value in agent_fields.items():
        if value is None or value == "":
            continue
        normalized = _norm_key(raw_key)
        canon = canonical_keys.get(normalized)
        if not canon:
            for alias_norm, alias_canon in canonical_keys.items():
                if alias_norm == normalized:
                    canon = alias_canon
                    break
        if not canon:
            for canon_key, aliases in _FIELD_ALIASES.items():
                if normalized == canon_key or normalized in {
                    _norm_key(a) for a in aliases
                }:
                    if canon_key in canonical_keys.values():
                        canon = canon_key
                        break
                    for alias in aliases:
                        if _norm_key(alias) in canonical_keys:
                            canon = canonical_keys[_norm_key(alias)]
                            break
                    if canon:
                        break
        if canon:
            out[canon] = value
    return out


def _missing_required_fields(
    schema: Dict[str, Any],
    fields: Dict[str, Any],
) -> List[Tuple[str, str]]:
    """``[(key, display_name), ...]`` for required fields neither supplied
    nor defaulted.

    ``content_profile_entry_fields.py`` (the actual create-time validator)
    falls back to ``field.default`` when a required field is omitted — a
    field with a default is satisfied either way, so only a required field
    with NO default and no supplied value is genuinely blocking. Checked
    here, before staging, so a create with a real gap (e.g. Pay run's
    ``frequency``, which has no default) surfaces one clear, actionable
    message instead of: stage → bless → execute-time failure → the create/
    drop-field retry dance in staging_executors.py futilely dropping the
    one field that can never be fixed by dropping it.
    """
    missing: List[Tuple[str, str]] = []
    for f in schema.get("fields", []) if isinstance(schema, dict) else []:
        if not isinstance(f, dict) or not f.get("required"):
            continue
        if f.get("default") is not None:
            continue
        key = str(f.get("key") or "")
        if not key or key in fields:
            continue
        missing.append((key, str(f.get("name") or key)))
    return missing


def _derive_title(text: str, *, limit: int = 80) -> str:
    """First non-empty line, trimmed and capped at ``limit`` chars."""
    for line in text.splitlines():
        candidate = line.strip()
        if candidate:
            return (
                candidate if len(candidate) <= limit else candidate[: limit - 1] + "…"
            )
    return "Untitled note"


def _format_fields_for_diff(fields: Dict[str, Any]) -> List[str]:
    """Return one-line-per-field markdown for the diff_human."""
    out: List[str] = []
    for k, v in fields.items():
        if isinstance(v, dict) and v.get("_raw") is not None:
            out.append(f"  - `{k}` = `{v['_raw']}` *(needs refinement)*")
        else:
            rendered = repr(v) if not isinstance(v, str) else v
            if len(rendered) > 60:
                rendered = rendered[:59] + "…"
            out.append(f"  - `{k}` = {rendered}")
    return out


def _filing_diff_human(
    *,
    track_title: str,
    entry_type_name: Optional[str],
    title: str,
    body_snippet: str,
    fields: Dict[str, Any],
    tags: List[Dict[str, Any]],
) -> str:
    """Render the human-readable filing diff (markdown)."""
    lines = [f"**File content** to *{track_title}*", "", f"- **Title:** {title}"]
    if entry_type_name:
        lines.append(f"- **Type:** {entry_type_name}")
    else:
        lines.append("- **Type:** *(not specified)*")
    if tags:
        rendered = ", ".join(f"`{t.get('name', '?')}`" for t in tags)
        lines.append(f"- **Tags:** {rendered}")
    if fields:
        lines.append("- **Fields:**")
        lines.extend(_format_fields_for_diff(fields))
    if body_snippet:
        lines.append("")
        lines.append(f"> {body_snippet}")
    return "\n".join(lines)


def _no_stage_candidates(
    *,
    message: str,
    filing_status: str = "unresolved",
    resolved_track: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "_kind": "filing_candidates",
        "filing_status": filing_status,
        "message": message,
    }
    if filing_status == "error":
        data["error"] = True
    if resolved_track:
        data["resolved_track"] = resolved_track
    return {"_no_stage": True, "data": data}


# --------------------------------------------------------------------------- #
# The stager
# --------------------------------------------------------------------------- #
async def stage_file_content(a: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``file_content`` change when track + entry type resolve from hints."""
    from app.agentive.personalization import get_filing_defaults
    from app.agentive.tooling.bindings import _bound_propose_principal
    from app.services.filing_resolution import (
        resolve_entry_type_for_track,
        resolve_track_for_filing,
    )

    uid = _bound_propose_principal()
    text = (a.get("text") or "").strip()
    fields_in = a.get("fields")
    if not text and isinstance(fields_in, dict):
        # Common agent mistake: the free-text body nested under `fields` (it
        # reads like a domain field named "text") instead of passed as the
        # top-level `text` param the schema actually requires. Recover it
        # rather than fail the whole card — and drop it from `fields` so it
        # isn't also offered to entry-type field normalization below.
        stray = fields_in.get("text")
        if isinstance(stray, str) and stray.strip():
            text = stray.strip()
            fields_in = {k: v for k, v in fields_in.items() if k != "text"}
            a = {**a, "fields": fields_in}
    if not text:
        return _no_stage_candidates(
            message=(
                "text is required — pass the entry's freeform content as the "
                "top-level `text` param, not nested inside `fields`."
            ),
            filing_status="error",
        )

    defaults: Dict[str, Any] = {}
    try:
        learned = await get_filing_defaults(user_id=uid, text=text)
        defaults = learned or {}
    except Exception:  # noqa: BLE001 — personalization is best-effort
        defaults = {}

    track_id = a.get("track_id") or defaults.get("track_id")
    track_hint = a.get("track_hint")
    focused = a.get("focused_track_id")
    type_hint = (
        a.get("type_hint") or a.get("entry_type") or defaults.get("entry_type_name")
    )

    track = await resolve_track_for_filing(
        uid,
        track_id=track_id,
        track_hint=track_hint,
        focused_track_id=focused,
    )
    if not track:
        return _no_stage_candidates(
            message=(
                "Could not resolve a track. Call integral_list_tracks and supply "
                "track_id or track_hint before staging."
            ),
        )

    if not type_hint or not str(type_hint).strip():
        return _no_stage_candidates(
            message=(
                "Entry type required. Call integral_get_track_schema and supply "
                "type_hint matching an entry type name."
            ),
            resolved_track={"id": track.id, "title": track.title or track.id},
        )

    entry_type = await resolve_entry_type_for_track(track, type_hint=str(type_hint))
    if not entry_type:
        return _no_stage_candidates(
            message=(
                f"type_hint {type_hint!r} did not match any entry type on "
                f"'{track.title}'. Re-read integral_get_track_schema."
            ),
            resolved_track={"id": track.id, "title": track.title or track.id},
        )

    schema = entry_type.form_schema if isinstance(entry_type.form_schema, dict) else {}
    explicit = a.get("fields")
    if isinstance(explicit, dict) and explicit:
        merged = _normalize_agent_fields(explicit, schema)
    else:
        merged = {}

    missing_required = _missing_required_fields(schema, merged)
    if missing_required:
        names = ", ".join(f"`{k}` ({n})" for k, n in missing_required)
        return _no_stage_candidates(
            message=(
                f"'{entry_type.name}' requires a value for: {names} — none has a "
                "default and none was supplied. Ask the user with integral_ask_user "
                "for whichever of these the user didn't already state, then retry "
                "with that key set in `fields`. Do not guess a value or stage "
                "anyway — the create will fail server-side and the user sees an "
                "opaque error."
            ),
            resolved_track={"id": track.id, "title": track.title or track.id},
        )

    raw_tags = a.get("tags") or defaults.get("tags") or []
    if raw_tags and isinstance(raw_tags[0], str):
        tag_names = [t for t in raw_tags if t]
        tags_for_diff = [{"name": t} for t in tag_names]
    else:
        tag_names = [
            t.get("name") for t in raw_tags if isinstance(t, dict) and t.get("name")
        ]
        tags_for_diff = [t for t in raw_tags if isinstance(t, dict)]

    track_title = track.title or track.id
    entry_type_name = entry_type.name
    title = (a.get("title") or "").strip() or _derive_title(text)

    payload: Dict[str, Any] = {
        "track_id": track.id,
        "title": title,
        "body": text,
        "entry_type": entry_type_name,
        "tags": tag_names or None,
        "fields": merged or None,
    }

    body_snippet = text if len(text) <= 200 else text[:199] + "…"
    diff_human = _filing_diff_human(
        track_title=track_title,
        entry_type_name=entry_type_name,
        title=title,
        body_snippet=body_snippet,
        fields=merged,
        tags=tags_for_diff,
    )
    summary = f"File content as “{title}” in {track_title} ({entry_type_name})"
    diff_machine: Dict[str, Any] = {
        "op": "file_content",
        "filing_status": "resolved",
        **payload,
        "_personalization": {
            "used_learned_track": bool(defaults.get("track_id")),
            "used_learned_type": bool(defaults.get("entry_type_name")),
            "matched_track_id": defaults.get("track_id"),
            "confidence": defaults.get("confidence"),
            "matched_count": defaults.get("matched_count"),
            "explanation": defaults.get("explanation"),
        },
    }
    return {
        "kind": "file_content",
        "summary": summary,
        "diff_human": diff_human,
        "diff_machine": diff_machine,
        "payload": payload,
    }
