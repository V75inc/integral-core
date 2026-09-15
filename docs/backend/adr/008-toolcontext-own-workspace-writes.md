# ADR-008 — Letting a bundle tool write into the caller's own personal workspace

**Status:** Accepted 2026-08-28 — implemented. Ruled: the narrow fused version.
**Date:** 2026-08-28
**Scope:** `backend/app/services/hooks/registry.py` (`ToolContext`), `backend/app/profiles/personal-context/tools/observe_entry.py`
**Related:** I-HOOK-02 §5 (scoped substrate access), ADR-006 / I-PC-01

## Context

Phase 1 shipped the `entry.create` / `entry.update` bindings against a
documented no-op. This is the note that says why the body cannot be written
yet.

`observe_entry` has to do one thing: when a person creates or edits an entry
in **any** workspace, record an observation in the `stream` track of their
Personal Context App, which lives in their **own personal** workspace. That
is two capabilities, and `ToolContext` has neither.

**There is no create.** The facade (`registry.py:167`) exposes `get_entry`,
`find_entries`, `find_entries_in_track_type`, `get_employee_compensation`,
`emit_audit`, `get_entry_system`, `update_entry_fields`, `put_attachment`,
`rollup_plan`. The only writes are a field-merge onto an entry that already
exists and an attachment put. A bundle tool cannot create a row.

**There is no way to name the target.** `ToolContext` is a plain dataclass
carrying `user_id`, `workspace_id`, `scope`, constructed per dispatch
(`entry_save_runtime.py:59`) with `workspace_id` set to the workspace the
entry lives in and `scope` to `entry:<id>`. Every read method scopes to that
`workspace_id`. The personal workspace is a different one, and the facade
offers no route to it.

The dataclass is not frozen, so a tool could technically assign
`ctx.workspace_id`. That is not a route, it is the hole the facade exists to
close: a bundle rewriting its own scope is exactly the thing I-HOOK-02 §5
forbids. Not doing that.

## What is actually needed

Two generic primitives. Neither names a bundle.

### 1. `ToolContext.create_entry(track_id, *, title, body=None, fields=None)`

Permission-gated per track with `resolve_role(user_id, "track", track_id)`
against the same role set `update_entry_fields` already uses, routed through
the normal entry-create service path so content-profile validation, change
events and audit all run as they do on the HTTP path.

Note what this alone fixes: **the gate becomes the caller's rights on the
target track, not the ambient `workspace_id`.** A tool that can name a track
the caller may write to does not need its scope switched. The absence of a
scope switch stops being the problem; the absence of a *create* is the whole
problem.

### 2. `ToolContext.own_bundle_track(track_key)`

Resolves the track whose manifest key is `track_key`, inside an App installed
from **the calling tool's own bundle**, in **the acting principal's own
personal workspace**. Returns `None` when any link is missing.

The calling bundle is known without anyone naming it: `register_workspace_tools`
already stamps `_bundle_slug` on every tool spec (`registry.py:64`), and the
same value reaches dispatch. So the substrate resolves "this bundle's track"
generically, and I-SUBSTRATE-01 holds.

The bounds are the interesting part, and they are the same ones ADR-006
established: own bundle, own personal workspace, own principal. A bundle
cannot reach another bundle's tracks, another workspace, or another user.
`resolve_personal_context` already does most of this walk and can be
generalized from one bundle to the calling one.

### Why not the alternatives

**Return a declarative result and let the substrate write it.** The tool
returns `{"observations": [...]}` and the hook framework persists them. Keeps
the facade closed — and puts the word "observation" in substrate code, which
is precisely the domain leak I-SUBSTRATE-01 exists to stop.

**Skip the hook; observe entry saves agent-side.** Phase 4's action already
has everything needed (`resolve_personal_context` + `dispatch_tool`), so a
second trigger there would need no facade change at all. But entry saves are
not turns: there is no interaction, no reply, and no walker to hang a
background action off. It would mean inventing a second dispatch path for
something the hook framework already delivers correctly.

**Let the tool mutate `ctx.workspace_id`.** Covered above. No.

## Decision requested

Add the two methods, bounded as described. Then `observe_entry`'s body is
short and the interesting parts are its refusals:

- ignore entries in the Personal Context App itself and in `agent-scratch` —
  otherwise the App observes its own observations forever;
- respect `attention_enabled` and `excluded_arenas`;
- record `workspace_ref` as the workspace the entry lives in, while writing
  into the personal one;
- never raise: an entry save must not fail because attention failed.

## Tests this needs

1. An entry saved in an organization workspace produces an observation in the
   person's personal-workspace `stream`, unstaged, with `workspace_ref`
   naming the org workspace.
2. An entry saved inside the Personal Context App produces nothing (the loop).
3. An entry saved in `agent-scratch` produces nothing.
4. `attention_enabled: false` produces nothing.
5. `create_entry` is refused when the caller has no rights on the target
   track — the gate is the track, not the ambient scope.
6. `own_bundle_track` returns `None` for another bundle's track key, and for a
   track in a workspace the caller does not own.
7. A tool that raises does not fail the entry save.
8. `.ci/bundle_facade_check.sh` stays clean — the tool still imports nothing
   but `ToolContext`.

## What shipped

Three methods, not the two this note proposed. The extra one is worth naming
rather than glossing.

- `create_entry_in_own_bundle_track(track_key, …)` — the fused write, as
  ruled.
- `own_bundle_view()` — the read counterpart. The tool has checks to make
  *before* writing (is this the App's own entry, is the behaviour switched
  off, is this workspace excluded), and every one of them needs the same
  resolution the write does. Returns only the calling bundle's own App id,
  workspace, settings and track ids.
- `track_kind(track_id)` — **the one this note did not anticipate.** The loop
  guard has two halves: the App's own tracks, which `own_bundle_view` covers,
  and `agent-scratch`, which it does not. `Track.kind` is the queryable
  discriminator I-SCRATCH-02 established (and title-matching is explicitly
  wrong there), so this exposes that one field, gated by the caller's read
  access.

Two smaller things changed on the way:

**`hook_point` now reaches the tool.** The entry-save payload carried
`entry_type`, `entry_id` and `entry_type_id` — so a tool bound to both
`entry.create` and `entry.update` could not tell them apart. One generic line
in `entry_save_runtime`; "made this" and "came back to this" are different
facts about a person.

**Actor and source are separate questions, and the row answers both.** The
write uses `actor_kind="human"`: the person's own save caused it, it runs
under their principal, and their role on the target track is what gated it,
so the audit actor is the same one their own write would carry. But the
CONTENT came from the App, so `create_entry_internal` grew an optional
`provenance` payload key and the facade marks the row agent-produced, naming
the bundle. Without that, a compiled page could later cite an observation as
something the person typed.

## What I would not decide without you

`create_entry` on the facade is a real widening: today no bundle tool can
create anything, and after this every trusted bundle can, in any track its
caller may write to. That is a larger surface than Personal Context needs —
this App only ever writes to its own two exempt tracks.

The narrower alternative is `create_entry_in_own_bundle_track(track_key, …)`,
which fuses both methods and cannot address any track outside the calling
bundle's App in the caller's own personal workspace. Less useful to the next
bundle; much harder to misuse.

**Recommendation: the narrow, fused version.** The general `create_entry` is
the kind of primitive that should arrive when a second caller genuinely needs
it, with that caller's requirements in hand — not speculatively, on the way
to something else.
