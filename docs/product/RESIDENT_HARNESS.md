# Resident Harness — Consolidated Specification

**Status:** Draft for architect review (companion to
[ADR-003](../backend/adr/003-singular-resident-harness.md))
**Supersedes as canonical source:** the harness material split across
CONCEPT §6, ARCHITECTURE §10.6 + §22.9, PRD Epic 5, and
[workspace-agent-profile.md](../backend/workspace-agent-profile.md) (which
remains the runtime reference for overlay composition).

---

## 1. Thesis

Integral is an **ops layer** on a pluggable harness, plus a resident mind.
Every deployment ships **one active harness binding** — by default the
embedded jvagent Orchestrator — that reads, writes, schemas, and coordinates
over the same graph, under the same access model, as every human user. A
first-class **Harness Switcher** selects which provider the ops layer augments
(Echo for smoke/dev, future harnesses). ADR-003 “singular resident” applies
**per active binding** (one mind + facets), not as a ban on provider selection.

The harness is the kernel of the coworker experience; Integral’s ops layer
(staging, skills overlay, permissions, MCP, feed/inbox) is what makes it
Integral:

- **Conversation is the primary surface.** The UI is a projection of the same
  substrate state. When a capability exists in the UI but not through the
  harness, that is a defect (see §7, tool parity), not a backlog nicety.
- **Always-on ops layer.** Current `main.py` registers the agentive package
  unconditionally. Historical `AGENTIVE_ENABLED` kill-switch language is
  retired as live behavior (flag absent from `app/config.py`). Features stay
  harness-first; UI-first designs with "agent support later" are rejected at
  plan review. Restoring substrate-only mode requires a deliberate kill-switch
  plan — do not document conditional load as if it already exists.
- **Cocreation is the defining experience.** Any user — technical or not —
  must be able to sit down with the harness and shape an environment that
  supports their productivity: schema it, populate it, view it, automate it,
  and have the harness carry work forward between sessions.

## 2. Facet model

The harness is singular; its **facets** are identity + policy projections of
the same mind. There are no separately configured personal/org/system agents.

| Facet | Principal | Policy posture | Audience |
|---|---|---|---|
| **personal** | Authenticated user | User's own effective permissions | The user, in-app chat |
| **org-facing** | Channel identity (WhatsApp / email / SMS) | Hard-narrowed: org-published subgraph only; no write escalation | External, possibly unauthenticated |
| **system** | Deployment principal | Platform routines (onboarding, maintenance, scheduled substrate work) | No direct audience |

Rules:

- **Facets only narrow.** Every facet resolves through the same
  `policy_engine.evaluate` path; fail-closed (I-AUTH-02) is unchanged. A facet
  can never see or do more than the strictest applicable principal.
- **One audit trail.** Every facet's actions land in the same ChangeEvent log
  with actor provenance; there is no per-facet shadow surface.
- **Configuration, not agents.** `AgentConfig` records simplify toward facet
  configuration (persona hints, channel bindings, schedule ownership) on the
  single harness — not independent agents with private skill sets.

## 3. Skill and tool layering

Two tiers, unchanged from the shipped overlay architecture
([workspace-agent-profile.md](../backend/workspace-agent-profile.md)):

- **Tier 0 — core skills** (`integral_*`): global SOPs shipped with the
  platform. Domain-free by invariant (I-SUBSTRATE-01); they encode *how to
  operate Integral*, never *what a CRM is*.
- **Tier 1 — workspace overlay**: skills and tools carried by installed App
  bundles, composed per-(workspace, user) at chat-turn time via
  `WorkspaceAgentProfile`. Domain logic lives here, reaching substrate only
  through the hook framework and `ToolContext` facade (I-HOOK-01).

The **tool manifest** (`backend/app/agentive/tool_manifest.yaml`) is
the single source of truth for the harness's dispatchable surface; the
catalogue advertises only tools with live bindings. Custom-skill (Python
handler) execution remains deferred behind the
[ADR-002](../backend/adr/002-contributed-skill-safeguard.md) safeguard phases.

The harness can author its own **Tier 1 workspace skills** at chat time —
`integral_author_skill` / `integral_update_skill` / `integral_delete_skill`
(propose/bless, `origin="workspace"`, `trust_tier="untrusted"`, declarative
markdown body only — no code execution, so this sits outside the ADR-002
safeguard scope entirely). This is distinct from app-bundled skills, which
still install only via a bundle's `operational-model.yaml` manifest at app-install time.
An author call may also scope the skill to a specific App (`app_id`) — it's
then private to that App's context by default (only surfaced when the
resident's active context is that App), and the App's own delete cascades
the skill with it.

## 4. Staging contract (write safety)

All harness mutations flow prepare → bless → execute:

- The harness **proposes**; the user **blesses** (or revokes) via staging
  cards; execution happens only on bless. Batches stage as one card.
- The identical staging primitives gate external MCP clients — there is no
  write path that bypasses staging short of an explicitly granted write scope.
- Approval language is human-readable (resource names, never raw ids).

This contract is what makes an always-on, proactive resident *safe*: the
resident may work continuously, but nothing lands without a human gate unless
the user has deliberately widened it.

## 5. Proactivity + memory (defining bet A)

A resident that only answers when spoken to is a chatbot, not an environment.
The proactive loop closes with four pieces, all of which have shipped
scaffolding today:

1. **Routines** — `RoutineTask` nodes + scheduler: recurring (`cron`) or
   one-shot (`run_at`) user-granted instructions. Scheduler runs due tasks as
   harness turns under the owning user's principal. Short reminders use
   `integral_schedule_task(run_at=…)` — not jvagent `queue_task` (disabled in
   this embed). Inbox and
   Background Tasks expose three controls:
   - **Pause** — skip future runs; keep the row; resume later. Does not abort
     an in-flight run.
   - **Stop** — soft-cancel (`status=cancelled`); abort an in-flight
     `origin=routine_task` turn on the bound thread; tombstone blocks
     app-schedule rematerialization on reinstall.
   - **Remove** — hard-delete the node (`cascade=False`); may rematerialize
     later for app-bundled schedules.
2. **App schedules** — `default_schedules` on bundle agents/skills dispatch
   named skills as resident turns (the App's operational heartbeat).
3. **Agent-initiated turns** — proactive results surface in chat threads
   (`POST /chat/threads/{id}/agent-turn` + WebSocket badges, already live) and
   stage like any other write.
4. **Memory** — per-user scratch track in the personal workspace
   (I-SCRATCH-01..05): the harness accumulates working knowledge, then
   **promotes** polished entries into real tracks with provenance
   (`derived_from`), through staging.

Event wake-ups (a minimal change-feed subscription so routines can react to
substrate changes rather than only the clock) complete the loop; the full
durable event stream remains its own roadmap item.

## 6. Knowledge reach (defining bet B)

Cocreation trust dies the first time the resident says "I can't see that"
about something on the user's screen. Two obligations:

- **Retrieval.** Deterministic graph queries plus hybrid semantic retrieval
  (I-RET-01..05: permission-filter-at-retrieval, no index bypass). Hybrid
  retrieval is live on the tool surface (`integral_retrieve` and related
  bindings); "Ask me anything in my workspace" must stay answerable within
  the caller's permissions.
- **Tool parity principle.** Every capability a human reaches through the UI
  must be reachable through the harness tool surface. As of the current
  `tool_manifest.yaml`, **100 tools are `existing`** and **3 remain `gap`**
  (`integral_bulk_move_entries`, workspace setup, onboard user — deferred).
  The parity matrix (manifest vs REST endpoint inventory) drives closure
  priority. New endpoints land with their tool binding or an explicit, dated
  parity exception.

## 7. External surface — MCP

External agents and systems (Claude, Cursor, user-built pipelines) connect
through **Integral's MCP server** — the one supported inbound interface:

- Identity from the token principal; workspace scope from `X-Integral-Scope`;
  fail-closed on both.
- Same tool catalogue, same dispatch, same policy gates, same staging, same
  audit trail as the resident. External clients default to read + propose;
  their proposals appear as staging cards inside Integral.
- There is **no A2A fabric** (retired, ADR-003): external agents talk to the
  substrate, not to the resident or to each other through Integral.

Outbound integration remains the connector framework (mirror model), unchanged.

## 8. Retired concepts

Per [ADR-003](../backend/adr/003-singular-resident-harness.md):

- A2A fabric (discovery, delegation, capability enforcement; I-A2A-01..05
  retired).
- Multi-surface BYOA (per-user PAT program, OpenAI Actions, direct REST as
  agent surfaces) — MCP is the external surface.
- Three separately configured agent kinds — replaced by the facet model (§2).

## 9. Design rationale (assessment)

Why this shape, given where the codebase actually is:

- **The substrate half of cocreation is done; the resident half is not.**
  Schema authoring via the harness works end-to-end (introspection tools →
  patch DSL → draft → diff → bless → publish, with migrations). Tool parity
  is largely closed (**97 `existing` / 3 `gap`** in `tool_manifest.yaml`).
  What is still missing is everything that makes the resident feel
  *resident*: routines are shell-only, schedules undispatch, and memory
  unshipped. Hence the two defining bets, pulled ahead of external polish.
- **A singular harness is the honest architecture.** The A2A fabric and
  multi-agent kinds were paper commitments (stubs, unenforced capabilities,
  no UI). Retiring them costs nothing users have, removes standing review
  tax, and sharpens the product story: one mind you cocreate with, one
  substrate everything reads and writes.
- **Facets keep the hard cases without a second agent.** The org-facing case
  is real (public agent over authorized knowledge only — a PRD persona), but
  it is a *policy posture*, not a different mind. One harness with hard
  narrowing is easier to reason about, audit, and secure than N agents with
  N skill sets.
- **Sequencing: resident loop → external surface → marketplace.** MCP mount,
  token UI, and ADR-002 hardening matter, but they multiply the value of a
  resident worth connecting to. Ship the defining experience first; harden
  the perimeter around something worth guarding.

## 10. Cross-references

- Decision record: [ADR-003](../backend/adr/003-singular-resident-harness.md)
- Overlay runtime: [workspace-agent-profile.md](../backend/workspace-agent-profile.md)
- Skill format: [skill-format-standard.md](../backend/skill-format-standard.md)
- Profile agent contract: [docs/operational-models/AGENT_CONTRACT.md](../operational-models/AGENT_CONTRACT.md)
- External surface: [BYOA.md](BYOA.md) (MCP-only scope)
- Credentials: [ADR-001](../backend/adr/001-model-credentials-byok.md)
- Safeguards: [ADR-002](../backend/adr/002-contributed-skill-safeguard.md)
