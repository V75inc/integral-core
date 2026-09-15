"""Hook handler for ``entry.create`` / ``entry.update`` — Personal Context.

The other door of the App. The post-turn attention action watches
conversations; this watches the person's own work: an entry they created or
edited, in any app, in any workspace.

What it records is deliberately thin. A saved entry is evidence that the
person is working on something, where, and when — the title and the track
say that much, and that is what promotion needs. It does NOT copy the
entry's body into the stream: the entry already exists and is already
theirs, and duplicating its contents into a second place would make the
observation stream a shadow copy of the workspace rather than a record of
what was noticed about the person.

Three refusals matter more than the write:

- **No feedback loop.** Entries in this App's own tracks are never observed.
  Without that, an observation is an entry, whose save fires this hook,
  which writes an observation, forever. The same applies to
  ``agent-scratch``: that is the resident's working memory about the work,
  not a record about the person, and observing it would fold one into the
  other.
- **Attention can be switched off.** ``attention_enabled: false`` stops this
  at the source — nothing written, not merely hidden.
- **Excluded arenas are invisible.** A workspace or App the person has
  listed is never observed at all.

Facade rule (I-HOOK-02 §5): this module imports nothing but ``ToolContext``.
The write goes through ``create_entry_in_own_bundle_track`` (ADR-008), which
resolves this bundle's App in the caller's OWN personal workspace — the
entry being observed may live anywhere, but what is noticed about a person
belongs to them.
"""

from __future__ import annotations

from typing import Any, Dict

#: Manifest key of the track observations land in.
_STREAM_TRACK_KEY = "stream"

#: Manifest key of the entry type. Named rather than left to the track
#: default: a typeless entry is invisible in every view that filters by
#: entry_type_keys, which is every view this bundle declares.
_OBSERVATION_TYPE_KEY = "observation"

#: How much of a title to keep. Entry titles are user text and can be long.
_TITLE_CAP = 200

#: Salience for a saved entry. Lower than a spoken commitment: that somebody
#: edited a record is weaker evidence about them than something they said.
#: Promotion decides what it means; this only says how loudly it knocks.
_SALIENCE = 0.35


async def run(payload: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    """Record one observation about a saved entry. Never raises.

    An entry save must not fail because attention failed, so every path
    returns a result dict rather than propagating. ``run_tool`` would
    otherwise surface the exception into the save path.
    """
    try:
        return await _observe(payload, ctx)
    except Exception as exc:  # noqa: BLE001
        return {"observed": False, "reason": "error", "detail": str(exc)[:200]}


async def _observe(payload: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    entry_id = str(payload.get("entry_id") or "").strip()
    if not entry_id:
        return {"observed": False, "reason": "no_entry"}

    entry = await ctx.get_entry(entry_id)
    if entry is None:
        # Not readable by this principal, or gone. Either way, not ours.
        return {"observed": False, "reason": "unreadable"}

    track_id = str(getattr(entry, "track_id", "") or "")

    view = await ctx.own_bundle_view()
    if view is None:
        return {"observed": False, "reason": "not_installed"}

    # --- Loop guard -----------------------------------------------------
    # An observation IS an entry, and saving it fires this hook again. If
    # this App's own tracks were observable it would observe itself forever.
    # Compared by track id rather than by title, which the person can edit.
    if track_id in set((view.get("track_ids") or {}).values()):
        return {"observed": False, "reason": "own_app"}

    # agent-scratch is the resident's working memory ABOUT THE WORK, not a
    # record about the person; folding one into the other would make both
    # less useful. `kind` is the queryable discriminator (I-SCRATCH-02) —
    # matching on the track's title is explicitly wrong there.
    if await ctx.track_kind(track_id) == "agent_scratch":
        return {"observed": False, "reason": "scratch"}

    # --- Settings gates -------------------------------------------------
    settings = dict(view.get("settings") or {})
    if settings.get("attention_enabled") is False:
        return {"observed": False, "reason": "attention_disabled"}

    excluded = {str(x).strip() for x in (settings.get("excluded_arenas") or []) if x}
    if excluded & {ctx.workspace_id, track_id}:
        return {"observed": False, "reason": "excluded"}

    title = str(getattr(entry, "title", "") or "").strip() or "an untitled entry"
    verb = "edited" if payload.get("hook_point") == "entry.update" else "saved"
    gist = f"{verb.capitalize()} “{title}”"[:_TITLE_CAP]

    new_id = await ctx.create_entry_in_own_bundle_track(
        _STREAM_TRACK_KEY,
        entry_type_key=_OBSERVATION_TYPE_KEY,
        title=gist,
        # The excerpt is a pointer, not a copy. The entry is the record; this
        # is the note that the person was working on it.
        body=f"{verb.capitalize()} “{title}”.",
        fields={
            "surface": "entry",
            "excerpt": title,
            "salience": _SALIENCE,
            "handled": "pending",
            "entry_ref": entry_id,
            "workspace_ref": ctx.workspace_id,
        },
    )
    if not new_id:
        return {"observed": False, "reason": "write_refused"}
    return {"observed": True, "observation_id": new_id}
