# Phase 4 — Proactive + Remaining Parity (triage)

**Status:** Triage / scoping (not yet built). Phases 0–3 of the
[Skills-as-Coordination roadmap](./workspace-agent-profile.md) are shipped; this
file enumerates and prioritizes the remaining parity work so it can be picked up
in scoped, verifiable slices rather than one grab-bag.

Why triage rather than build-now: each item below is independent and several are
substrate-touching. They are sequenced by leverage — the gap **reads** that turn
the Phase 1b folded coverage from prose-with-caveats into real capability come
first; the larger proactive/settings machinery last.

---

## P1 — Gap READ tools that unblock the Phase 1b folds

The Phase 1b folds (orientation / sharing / tags / comments / relations / briefing)
were authored against existing tools and **explicitly marked these reads
"not-yet-available"** with substitutes. Wiring them (reads → lower risk than
writes; most have a backing endpoint already) makes the folded coverage real.

Each is a route- or service-backed read: flip manifest `status: gap → existing`,
add a `ToolBinding(handler_ref=…)` (or `service_ref`) with a `param_map`/`query_map`
in `tooling/bindings.py`, mirror an existing read (e.g. `integral_list_tracks`).

| Tool | Backing | Unblocks fold |
|------|---------|---------------|
| `integral_get_scope` | `request_scope` / scope resolver | orientation (identity/workspace) |
| `integral_list_workspaces` | `workspace_permissions.list_accessible_workspaces` | orientation |
| `integral_get_access` | `GET /{app\|track\|entry}/{id}/access` (polymorphic — mirror `integral_get_access` access.py) | sharing (workspace) |
| `integral_list_tags` | `GET /api/tags` (`app/api/tags.py`) | tags (entries) |
| `integral_list_comments` | `GET` comments on entry (`app/api/comments.py`) | comments (entries) |
| `integral_get_related` | `GET /api/entries/{id}/related?relation=` | relations (entries) |
| `integral_get_feed` | feed endpoint | briefing (insights) |
| `integral_list_notifications` | already partially present — confirm dispatchable | briefing |
| `integral_list_views` | `GET` views on track | review coordinator grounding |

**Verify:** each appears in `build_tool_catalogue()`; a dispatch read test per tool;
re-grep the folded SKILLs and replace each "not-yet-available" caveat with the now-live tool.

## P1 — Bulk move (the one deferred write enabler)

`integral_bulk_move_entries` was left `gap` in Phase 0 — no re-parent (change
`CONTAINS`) primitive exists. Needs: a track re-parent operation (service +
endpoint) that revalidates the target track's schema compatibility per entry,
then a `bulk_move_entries` staging kind looping it (fail-stop, like the other bulk
ops). Unblocks `integral_organize`'s "move Q3 → Q4" example fully (today it
re-creates rather than moves).

## P2 — Anchor track as a distinct affordance

`integral_link_entries` already materializes `ANCHORS` when the relation field
targets a track. A dedicated `integral_anchor_track` would be sugar over the same
path — only add if the modeling SOP wants a clearer verb; otherwise close as
"covered by link_entries."

## P2 — Audit the audit findings (skill-bundle-audit.md remediations)

From [`skill-bundle-audit.md`](./skill-bundle-audit.md): bring the 5 pre-existing
skill bundles to the quality bar (thin stubs, missing sections), fix the 3 phantom
tool names referenced in fixed-asset/inventory prose, and decide keep-or-migrate
for the portfolio/sales older-paradigm skills (`private: true`, no `extends`, no
tool coordination — they never reach the resident overlay).

## P2 — Comment edit/delete, view delete, share-link mint/revoke, exclusions, invite

Remaining write gap tools (`integral_edit_comment`, `integral_delete_comment`,
`integral_delete_view`, `integral_mint_share_link`, `integral_revoke_share_link`,
`integral_set_exclusion`, `integral_remove_exclusion`, `integral_invite`). Each is
a thin stager → existing endpoint, same pattern as the Phase 0 CRUD fills. Wire as
demand surfaces.

## P3 — Settings templating (`{{settings.*}}`)

App settings are captured on the workspace profile but the render pass is deferred
(see `workspace-agent-profile.md` "Out of scope"). A materialization-time pass that
interpolates `{{settings.key}}` into a skill body before it reaches the
orchestrator lets a bundle parameterize its SOPs per install. Lives in
`workspace_agent_profile.compose_workspace_agent_profile`.

## P3 — Proactive skill dispatch (`default_schedules`)

App agents can declare `default_schedules`; today nothing fires them. A scheduler
that, per schedule, spawns a resident turn invoking a named skill (e.g. a weekly
`pipeline_review`) would make Apps proactive. Substantial — needs a scheduler, a
headless turn path, and per-tenant rate/credit guards (note the BYOK key mode
interaction). Design before build.

## P3 — Private-skill visibility policy

`private: true` skills (portfolio, sales) are excluded from the resident overlay by
design. Decide the product behavior: resident-invisible-but-app-agent-callable vs a
per-skill resident opt-in. Affects `get_callable_skills`.

## P4 — Walker-based multi-hop reads

Per `agentive/CLAUDE.md`, no walkers exist yet. Relation-read tools (`get_related`
and friends) that traverse >1 hop are the canonical place to introduce the first
`agentive/walkers/` walker (M6 agent-memory phase) instead of Python `.nodes()`
chains.

---

## Shipped since triage

- **Attachment reads (`integral_list_attachments`, `integral_get_attachment_text`).**
  Both flipped `gap → existing`, backed by `app/services/attachment_agent.py`
  (list strips the extracted-text body to a summary; text read caps for the
  model context), bound via `service_ref`, gated on `entry.read`. New
  `integral_attachments` base skill owns list/read/deliver; chat renders the
  list as a download card. Contract: `docs/backend/attachment-agent-contract.md`.
  Still deferred: `integral_attach_file` (agent-staged upload) and global
  full-text search over `extracted_text`.

---

## Sequencing

P1 reads + bulk-move first (unblocks shipped SOP coverage, low risk). Then the P2
write gap tools + audit remediation as demand surfaces. Settings templating and
proactive schedules (P3) are their own design+build efforts — schedule after the
read parity lands and the BYOK credit-guard interaction is settled.
