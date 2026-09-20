# ADR 003 — Singular resident harness (agentive-primary, faceted, A2A retired)

**Status:** Proposed (pending architect review — this record is the review artifact)
**Date:** 2026-07

## Context

Integral's concept evolved through several versions. Earlier framings described a
multi-vector agentive world: three collaboration vectors (H2H / H2A / A2A) "as
equals" (CONCEPT §3), an A2A fabric with agent discovery and delegation
(ARCHITECTURE §22.7, ROADMAP M5-D1, INVARIANTS I-A2A-01..05), a multi-surface
BYOA program (per-user PATs, OpenAI Actions, direct REST), and three separately
configured agent kinds (`AgentConfig.scope ∈ {personal, org_facing, system}`).

What actually shipped converged elsewhere. The embedded resident cockpit
(jvagent Orchestrator + 13 base `integral_*` skills + ~90-tool manifest +
staging prepare→bless→execute + per-workspace skill overlay) is mature and is
the surface users experience. A2A remains stub-only
(`agentive/agent_actions/delegate.py`; capabilities stored, never enforced).
BYOA was already scope-reduced (2026-06-03) to Integral's MCP surface. The
org-facing and system agent kinds exist as node/edge scaffolding with no UI.

The docs therefore describe a world we are no longer building, and the
divergence taxes every substrate-touching plan review.

## Decision

1. **Singular resident harness is the standard.** Every Integral deployment
   ships one resident harness (Claude-like) **per active harness binding**:
   core skill/tool collection (tier 0, `integral_*`) plus App-configured
   skill/tool overlays per workspace. The harness is the platform kernel;
   Integral is the ops layer (staging, skills, MCP, permissions) on top.
   A **Harness Switcher** that picks the provider (jvagent / Echo / future)
   is intentional and is **not** A2A revival — it selects which mind the ops
   layer binds, not a fleet of peer agents.

2. **Agentive-primary design rule.** Conversation is the primary surface; the
   UI is a projection of the same substrate. The agentive ops layer is
   **always-on** in current code. Historical `AGENTIVE_ENABLED` as a
   substrate-only kill-switch is **not** a live boot gate (absent from
   `app/config.py`). No feature may be designed UI-first with harness parity
   as an afterthought. Parity obligations flow UI ← harness, not the reverse.
   This resolves ARCHITECTURE §22.12 Q3 in favour of agentive-primary and
   reframes §10.6.

3. **Facet model replaces agent multiplicity.** The three `AgentConfig` scopes
   collapse into **facets of the one resident harness** — identity + policy
   projections, not separate agents:
   - **personal facet** — authenticated user principal; user's own policy.
   - **org-facing facet** — channel-identity principal (WhatsApp/email/SMS,
     unauthenticated audience); hard-narrowed policy, org-published subgraph
     only.
   - **system facet** — deployment principal for platform routines.
   Facets only ever narrow (reuses I-AUTH-02 fail-closed narrowing unchanged).
   `AgentConfig` simplifies toward facet/policy configuration records on the
   single harness.

4. **A2A is retired, fully.** No agent-to-agent fabric, discovery, or
   delegation inside Integral. Inbound external interop = the MCP server
   (users optionally connect their own agents/systems). Outbound = connectors
   (mirror model), unchanged. The "three vectors as equals" framing reduces to:
   humans and the resident harness cocreate over the substrate; external
   agents reach the same substrate via MCP under the same policy gates.

5. **Near-term defining bets: (a) proactivity + memory, (b) knowledge reach.**
   The cocreation loop is currently reactive-only (propose→bless works;
   resident-initiated work is shell-only). Pull forward, ahead of external MCP
   polish and marketplace hardening:
   - RoutineTask scheduler completion, `default_schedules` dispatch,
     agent-initiated turns in product use, scratch memory + promotion
     (I-SCRATCH-01..05 already specified), minimal event wake-ups.
   - Hybrid retrieval (I-RET-01..05 already specified) and closure of the
     gap-status tools in `tool_manifest.yaml` — the harness must reach
     everything a human can reach (tool-parity principle).

6. **Skill ecosystem layering is unchanged.** Two tiers (core `integral_*` +
   App overlays) stand. Custom-skill runtime stays deferred behind the ADR-002
   safeguard phases; "App bundles carry skill bundles" intent is preserved and
   sequenced after the resident loop is defining-feature complete.

7. **One consolidated spec.** The harness contract — previously split across
   CONCEPT §6, ARCHITECTURE §10.6 + §22.9, PRD Epic 5, and
   workspace-agent-profile.md — consolidates into
   [docs/product/RESIDENT_HARNESS.md](../../product/RESIDENT_HARNESS.md);
   the split documents point at it.

## Consequences

### Documentation (Phase B of this effort)

- ARCHITECTURE.md: §22.7 marked retired → this ADR; §10.6 reframed (ops flag,
  not design ceiling); §22.12 Q3 resolved; facet-model note on agent nodes.
- ROADMAP.md: M5-D1 (A2A fabric) struck; proactivity+memory and retrieval
  resequenced forward; MCP polish/marketplace after; logged via §10 revision
  protocol.
- BYOA.md: residual multi-surface language tightened; facet alignment.
- CONCEPT.md / PRD.md: "three vectors as equals" reframed; A2A mentions
  updated; Epic 5 points at RESIDENT_HARNESS.md.
- INVARIANTS.md: I-A2A-01..05 marked **retired** (IDs kept for history; no
  new-code enforcement).
- backend/app/agentive/AGENTS.md + root AGENTS.md agentive references updated.

### Code retirement (flagged for pod planning; not executed with this ADR)

- Delete `backend/app/agentive/agent_actions/delegate.py` (stub-only).
- Drop capability-enforcement plans tied to I-A2A (capabilities field may
  remain as inert metadata until the facet refactor decides its fate).
- Collapse `AgentConfig.scope` kinds into facet configuration — a
  substrate-touching plan (nodes/edges: `HasAgentConfig`, `HasOrgAgent`,
  `HasSystemAgent` survive as facet wiring; naming review in that plan).
- Tool-manifest gap-tool closure plan (parity matrix drives priority).
- RoutineTask scheduler completion plan.

### Facet-collapse migration path (Wave 3 scaffolding; full collapse deferred)

Safe scaffolding landed without breaking boot:

1. **Additive field.** `AgentConfig.facet: Optional[str] = None` —
   preferred discriminator for new readers. When `None`, fall back to
   legacy `scope` (`personal` | `org_facing` | `system`). Writers that set
   `facet` MUST keep `scope` in sync until dual-field retirement.
2. **Edge deprecation (comments only).** `HasOrgAgent` / `HasSystemAgent`
   docstrings mark the types deprecated; runtime wiring unchanged so
   uplink registration and I-GRAPH-01 reachability keep working.
3. **Dedicated follow-up plan still required to:**
   - backfill `facet = scope` on existing rows;
   - switch uplink / MCP / registry readers to prefer `facet`;
   - collapse org/system edges onto one harness edge family keyed by facet;
   - remove dual `scope`/`facet` and the deprecated edge classes.

Do not delete `HasOrgAgent` / `HasSystemAgent` or stop writing `scope` in
this wave — boot and system-agent registration still depend on them.

**Provider switcher (2026-09-08):** A Harness Switcher that selects the
active provider binding (jvagent / Echo / future) is allowed and intentional.
It is **not** A2A revival — it picks which mind the ops layer binds, not a
fleet of cooperating peer agents.

### What this does NOT change

- Access model: humans, resident harness, and external MCP clients continue
  through the identical permission layer, staging primitives, and audit trail
  (no shadow CRUD; no agent-only bypass).
- Connectors (outbound mirror model) and their invariants (I-CON, I-SYNC).
- Content-profile agent contract (introspection-first tools + patch DSL).
- ADR-001 credentials model; ADR-002 safeguard design (its build phases
  re-rank behind the defining bets, they do not change shape).

## Alternatives considered

- **Park A2A as dormant** (keep invariants + stubs) — rejected: the stubs and
  invariants cost review attention on every substrate plan while the MCP
  surface already covers external interop; nothing in the facet model
  forecloses re-introducing coordination later if federation ever demands it.
- **Keep three agent kinds atop one engine** — rejected: three configured
  agents with separate skills/personas/schedules re-creates the fragmentation
  Integral exists to remove; identity + policy facets achieve the org-facing
  and system use cases with one mind and monotonic narrowing.
- **Two-tier compromise (separate org-facing agent)** — rejected for the same
  reason; the unauthenticated-audience risk is a *policy* property of the
  org-facing facet, not grounds for a second agent.
