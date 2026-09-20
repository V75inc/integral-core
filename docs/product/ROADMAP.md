# Integral: Product Roadmap

**Document Version:** 1.4
**Date:** 2026-09-15
**Visionary + Architect:** Eldon Marks
**Builder:** Internal AI-engineering team (10 engineers + hire capacity), each leveraging Claude Code / equivalent coding-model pipelines under GSD discipline
**Status:** Aligned with CONCEPT v5.0, PRD v5.0, ARCHITECTURE v2.0 (incl. §22 Vision-Aligned Directions); aligned with manifest STRATEGIC_POSITION.md v1.0 and VISIONARY_PLAYBOOK.md v1.0
**Horizon:** ~12–18 months wall-clock to v1.0, bounded by milestone gating and architectural coherence — *not* by engineer headcount alone

**Companion docs:** [integral_manifest STRATEGIC_POSITION.md](../../../integral_manifest/00-master/STRATEGIC_POSITION.md) (why we win/lose, what we watch) · [integral_manifest VISIONARY_PLAYBOOK.md](../../../integral_manifest/00-master/VISIONARY_PLAYBOOK.md) (how Eldon operates) · [PRD.md](PRD.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [CONCEPT.md](CONCEPT.md).

---

## 1.0 Purpose

This roadmap operationalizes Integral's mission — *be the AI-native knowledge substrate that humans and AI agents read, write, schema, and coordinate over under one access model* — into a sequenced, GSD-decomposable plan that takes the project from its current state (substrate + Phase 2–5 sharing primitives + Content Profile Pillars 1–4 + scaffolded agentive layer) toward v1.0.

It exists alongside [PRD.md](PRD.md) (what we build), [ARCHITECTURE.md](ARCHITECTURE.md) (how it works), and [CONCEPT.md](CONCEPT.md) (why it matters). This document answers **when**, **in what order**, and **how the orchestrator dispatches the builder**.

The roadmap is grounded in two product convictions:

1. **AI product arbitrage** — Integral is positioned to capture value at the seam where the AI tooling ecosystem (Claude, OpenAI, agent frameworks, MCP, skills, routines) meets organizations' fragmented domain knowledge. The arbitrage is the *substrate*: a singular, schema-coherent, permission-coherent universe of knowledge that any compliant agent can plug into.
2. **Conformable, not opinionated** — Integral does not prescribe one workflow. The Content Profile substrate lets every user, team, or company conform the space to their domain. The platform's job is to make that conformance trivial — via direct manipulation, via conversational AI authoring, and via mirrored data from the tools the organization already uses.

## 1.1 Execution Model — Team-Scaled AI-Engineering Pipeline

Integral is built by an internal team of ~10 AI engineers, each leveraging Claude Code (or equivalent) coding-model pipelines under GSD discipline. Capacity to hire more engineers as milestone scope demands. Eldon operates as **product visionary + architect**, not as primary implementer.

The shape:

```
                  Eldon (Visionary + Architect)
                          |
                  Engineering Lead(s)  —  one per ~5 engineers
                  /       |        \
            Pod A       Pod B      Pod C ...
         (3-4 engs)  (3-4 engs)  (3-4 engs)
            Each engineer drives 1-2 AI coding pipelines per phase
```

**Pods** own one or two themes per milestone (e.g. Pod A on Resident AI Surface, Pod B on Connectors). Pods are reformable per milestone, not permanent reporting structures. Each engineer dispatches their assigned phase through their AI coding pipeline; engineering leads review; Eldon spot-checks substrate-touching changes and runs a per-milestone architecture review.

Implications that shape the rest of this doc:

- **Pace is bounded by milestone gating and architectural coherence**, not by headcount. With ~10 engineers × ~2 active phases each, the throughput cap is on Eldon's review bandwidth and the engineering leads' planning/review quality — not on lines-of-code-per-week. Wall-clock estimates in §4 assume one milestone-close cycle every ~6–10 weeks.
- **Themes are parallel workstreams.** Multiple themes execute concurrently across pods. A typical milestone might run Theme A (one pod) alongside Theme B (a second pod) plus a Theme F hardening slice (third pod). Pods are coordinated through the GSD pipeline + per-milestone architecture review, not standing meetings.
- **Architectural coherence is the load-bearing wall.** With ~10 engineers × ~2 pipelines each = up to 20 concurrent code paths shaping the substrate, drift compounds fast. Mitigations: `../INVARIANTS.md` (substrate invariants), per-milestone architecture review (Eldon, ~4–6 hrs at close), 1-in-5 substrate-touching PR spot-check (Eldon), Decision Records for substrate-invariant changes, AI-pipeline parallelism cap of 2 per engineer with mandatory teammate code review. See [VISIONARY_PLAYBOOK §3](../../../integral_manifest/00-master/VISIONARY_PLAYBOOK.md).
- **Verification rigor is non-negotiable.** Every phase ships with `/gsd-verify-work` + `/gsd-code-review` + (where applicable) `/gsd-secure-phase`. Engineering leads enforce; Eldon spot-checks. Skipping these is the single largest risk in a multi-pipeline model — locally-correct code that violates global invariants is the most common failure mode.
- **Eldon's role is brief author + architecture reviewer + decision authority**, not phase orchestrator. Each engineer orchestrates their own GSD pipeline. Eldon authors the milestone brief (the "why + must-do + must-not + done-looks-like" doc), reviews plans for substrate-touching phases, runs the milestone architecture review, and signs Decision Records.
- **Engineering leads own** per-phase planning quality, code review standards, AI-pipeline orchestration patterns, and surfacing architecture concerns upward. They do not own *what* to build (that's Eldon-as-visionary).
- **The orchestrator (Eldon) owns external dependencies** that engineers cannot: vendor-side credentials (Stripe, OAuth registrations across Google / Microsoft / Atlassian / Slack / Anthropic / OpenAI), DNS, SOC 2 auditor relationships, design-partner / engagement-client introductions, methodology refusal calls. Engineers scaffold the integration code; the visionary owns the relationships.
- **Roadmap revisions are Eldon's call.** Engineering leads or engineers flag candidates in the Revision Log; Eldon promotes them.
- **Engagement-driven priorities feed the roadmap.** Delivery ops lead reports engagement signals every two weeks (see [VISIONARY_PLAYBOOK §7](../../../integral_manifest/00-master/VISIONARY_PLAYBOOK.md)). Backlog entries promote at milestone close, not mid-milestone.

## 1.2 Decision Rights (Roadmap-Relevant Subset)

Full matrix in [VISIONARY_PLAYBOOK §2](../../../integral_manifest/00-master/VISIONARY_PLAYBOOK.md). Roadmap-relevant rows:

| Decision class | Visionary (Eldon) | Architect (Eldon) | Eng Lead | Delivery Ops Lead |
|---|---|---|---|---|
| Roadmap milestone scope | **Decides** | Reviews | Inputs | Inputs |
| Substrate invariant change | Reviews | **Decides** | Inputs | – |
| Non-substrate refactor | Reviews if cross-cutting | Reviews | **Decides** | – |
| Feature priority within a milestone | **Decides** | Reviews | Implements | Inputs (from engagements) |
| Sector overlay package commit | **Decides** | Reviews | Implements | Inputs |
| LLM vendor relationships | **Decides** | Reviews | Inputs | – |
| Pivot trigger activation | **Decides** | Reviews | – | Inputs |

---

## 2.0 Starting Point (Today — 2026-08-02)

What ships today and forms the foundation:

| Layer | State |
|---|---|
| **Substrate** | Workspace → App (`WorkspaceApp` discriminator) → Track → Entry / EntryType / Tag / View primitives in production. Personal workspace auto-created per user; org-kind workspaces support member pools. |
| **Content Profile schema** | Manifest v1 + v1.1 (composable field types, view types, plugins). Draft / publish / diff / discard lifecycle. Atomic swap on publish. Migration framework. Frontend mirrors (widget + field-type registries, composable meta-widgets). |
| **Access model** | `policy_engine.evaluate()` as the single authorization entry point (legacy `resolve_role` / `can_*` helpers delegate). Inheritance with explicit deny (`EXCLUDED_FROM`). Five collaboration roles (`owner / admin / editor / commenter / viewer`). Phase 2–5 sharing primitives (collaborators, exclusions, share-links, resource invitations, `/me/shared`, `/me/invitations`). Backend-authoritative workspace scope (`X-Integral-Scope`). Cross-workspace guest auto-grant. |
| **Agent contract** | Introspection-first MCP tools (`integral_describe_substrate`, `integral_describe_profile`, `integral_get_profile_draft`, `integral_propose_profile_revision`, `integral_diff_profile_draft`, `integral_publish_profile_draft`, `integral_discard_profile_draft`) backed by a patch-DSL. |
| **Resident harness** | Always-on under `backend/app/agentive/`. Singular resident (faceted personal/org-facing/system), ~99 live tools in `tool_manifest.yaml` + dispatch bindings, staging (prepare→bless→execute), workspace skill overlay, MCP surface (`backend/app/agentive/mcp/server.py`) for external agents, conversation/thread plumbing. Per [ADR-003](../backend/adr/003-singular-resident-harness.md). |
| **Hybrid retrieval** | Mostly landed: `POST /api/retrieve` (`backend/app/api/retrieve.py`) — graph, semantic, and hybrid modes with permission filter at retrieval time (I-RET-01). Re-ranking and broad cross-workspace query polish remain roadmap. |
| **Policy engine** | Landed: `policy_engine.evaluate()` + Policy nodes, `HAS_POLICY` edges, agent/connector fail-closed defaults. Admin grant-chain UX (single readable view of every grant for a subject) still thin — M7 target. |
| **Connectors (mirror-model integrations)** | Partially built: `SyncConnector` framework + sync runtime/scheduler (`backend/app/services/connectors/`), `Connector` node CRUD, reference connectors Gmail, QuickBooks, GitHub Issues (`backend/app/agentive/connectors/`). Jira, Drive, Slack, HRIS/CRM/Calendar and marketplace UX remain roadmap. |
| **Scratch memory** | Mostly built: per-user scratch track provisioning + promotion (`backend/app/services/agent_scratch.py`, I-SCRATCH-01..05). Per-conversation sticky state and cross-session memory namespace polish remain roadmap. |
| **Attachments / uploads** | Resumable chunked uploads, metadata pipeline, preview pipeline, thumbnails. |
| **Real-time** | Polling baseline; agent-turn WebSocket badges live; durable workspace event stream deferred (M7). |
| **GTM surface** | Internal/dev only. No pricing, no admin tier, no mobile, no on-prem. |

**M1 note:** Substrate, harness, retrieval, policy, connectors, and scratch have advanced ahead of M1 exit criteria for methodology productization (Three-Question Filter, Decision/Refusal Records, Three-Horizon dashboard, Use-Case Canvas, sector overlay) and LMS-on-Integral — those proserve-deliverable items may still be open while the foundation below them has hardened.

Everything in this roadmap layers onto this foundation.

---

### 2.1 Foundation-first priority reframe (draft — 2026-09-15)

The next planning boundary adopts a **bottom-up** execution order. Integral
will first become a separable, extensible foundation; then make its operating
services dependable; then prove the foundation through paid Apps and cohesive
user experiences. This reframe does not revoke the outcomes in the milestones
below. It changes their implementation order and defines a stronger completion
standard for the platform beneath them.

The target is an open Integral base that can be maintained, tested, released,
and deployed without any first-party or commercial domain App installed. Apps
must be independently distributable packages—including any App-owned views,
operations, skills, and integrations—and must use a stable extension contract
rather than importing core internals. Select Apps and Content Profile packages
may then be commercial products without turning entitlement checks into a
cross-cutting concern throughout the base.

The execution order is:

1. **Foundation boundary and extension contract.** Separate core substrate,
   generic view/runtime facilities, and package registry from domain bundles;
   publish compatibility, lifecycle, migration, and security conventions for
   data, operations, skills, views, and integrations.
2. **Trust and operational services.** Harden policy explanation, audit and
   provenance, event delivery, retrieval correctness, background execution,
   observability, backup/restore, and schema evolution.
3. **SaaS commerce and entitlement.** Add a provider-neutral entitlement
   service with Stripe Billing as the first provider. Gate paid packages and
   metered capabilities on the server, with a usable customer billing surface.
4. **Reference Apps and integration loops.** Build Organization/HRM and other
   commercial Apps as external-package proofs of the contract; complete a small
   number of connector-to-decision loops rather than broadening the catalog
   prematurely.
5. **Experience and productization.** Turn the resident, App installation,
   governance, and workspace onboarding into a coherent operating experience;
   then complete mobile-responsive, deployment, support, and public-documentation
   requirements for v1.0.

The detailed architecture and acceptance criteria are in
[FOUNDATION_EXTENSION_SAAS.md](FOUNDATION_EXTENSION_SAAS.md). This is the
controlling priority lens for new roadmap phases until explicitly revised.

**F0 status:** Phase One + monorepo unbundle shipped — Core
`backend/app/profiles/` is seeds-only; domain Apps under `packages/apps/`.
Physical public `integral-core` extract uses dependency pin (see
[INTEGRAL_CORE_EXTRACT.md](INTEGRAL_CORE_EXTRACT.md)).

**F1 status:** Phase One — Admin Forensic Loop shipped on
`staging/foundation` (explain API, App operations, Audit Log UI, provenance
correlation). Remaining F1 bullets (retrieval, migration UX, broad
observability) are later waves.

**F2 status:** Phase One — App-owned declarative views shipped
(completion test 4 declarative half). Signed FE view-plugin hot-load later.

**F3 status:** Phase One — manual entitlement kill-switch + Core App export
shipped (completion test 5). Stripe + meters + portal = Wave Two.

**Foundation Phase One:** **COMPLETE** — next: publish open `integral-core`
and pin commercial Integral to Core tags ([CORE_PIN.md](CORE_PIN.md)).

### Existing roadmap outcome mapping

The F-series is a priority and dependency reframe, not a second competing
roadmap. New phase briefs must name their F-series home and, where applicable,
the legacy outcome they satisfy.

| Existing roadmap outcome | Foundation-first home |
| --- | --- |
| Content Profile/App lifecycle, migration and package-authoring primitives | F0 — Core separation and compatibility baseline |
| Audit, provenance, policy explanation, durable events, retrieval correctness and observability | F1 — Trustworthy substrate operations |
| Connectors, routines, staged operations, graph query, memory and App-owned runtime behavior | F2 — Extension runtime and operating services |
| Plan tiers, billing, usage, hosted/self-hosted commercial operation and entitlement enforcement | F3 — SaaS commerce, entitlement and deployment |
| Organization/HRM/Payroll, sector packages, and hero connector-to-decision workflows | F4 — Reference Apps and proven vertical loops |
| Resident UX, conversational configuration, workspace onboarding, governance UX, LMS/other packaged experiences, responsive product and support surfaces | F5 — Cohesive experience and v1.0 operations |

The M1 methodology and LMS outcomes remain required. They move into F4/F5
only after the contracts they depend on are stable; they must not become an
implicit exception to the Core/App separation.

---

## 3.0 Strategic Themes (Areas of Work)

Six themes describe *what* gets built. They are **not** parallel workstreams under separate teams — they are areas of work that the orchestrator schedules into milestones. Most milestones execute serially; selected slices run in parallel via worktrees (`gsd-workstreams`) when their dependencies are independent (e.g. a connector phase alongside an unrelated frontend polish phase).

### Theme A — Resident AI Surface

**Outcome:** The resident AI becomes the *primary* surface for non-trivial work in Integral — schema authoring, view configuration, entry capture, cross-track synthesis — without ever bypassing the access model. The UI is the organized lens; conversation is the verb.

**Scope includes:**
- Conversational config UI for views, profiles, entry types, tags (text-Integral-like-a-colleague).
- Agent inbox / persistent conversation surface in the frontend, wired to the conversation/thread plumbing already in `agentive/`.
- Substrate-aware prompts (every agent turn consults `integral_describe_substrate` first).
- Resident-AI-as-author for Content Profile revisions: propose → diff → publish flow with human-in-the-loop.
- Resident proactivity: routines, scheduled skills, agent-initiated turns, scratch memory + promotion (see Theme D, resequenced per [ADR-003](../backend/adr/003-singular-resident-harness.md)).

### Theme B — Connector Framework + Native Integrations

**Outcome:** Integral becomes a believable single source of truth because the data is *already there*. The connector framework is a first-class subsystem; each connector mirrors external knowledge into the graph as typed, tagged, provenance-stamped nodes that agents query through the same APIs as native entries.

**Scope includes:**
- Connector framework (auth handshake, sync engine, deduplication, conflict resolution, backfill, incremental).
- Provenance metadata as first-class node property (`source_system`, `source_id`, `source_version`, `last_synced_at`).
- Per-connector Content Profile package (Jira maps to its own EntryTypes / Tags / Views).
- Wave 1: **Jira**, **Gmail**.
- Wave 2: **Google Drive**, **QuickBooks**, **Slack**.
- Wave 3: **HRIS** (BambooHR/Rippling), **CRM** (HubSpot/Salesforce), **Calendar** (Google/Outlook).
- Connector marketplace UX (browse, authorize, configure, monitor).

### Theme C — BYOA Uplink Maturation

**Outcome:** Any organization with a Claude or OpenAI subscription can plug their existing agents (custom GPTs, Claude Projects, skills, routines) into Integral with no shadow CRUD path. BYOA agents see exactly what their human counterpart sees — same scope, same permissions, same audit trail.

**Scope includes:**
- MCP-first API surface — every meaningful REST endpoint exposable as an MCP tool.
- Native Claude integration path (Claude Projects, Claude skills mapping to Integral capabilities).
- Native OpenAI integration path (custom GPTs reading via MCP, routines invocable from Integral).
- MCP connection management (endpoint, token/approval lifecycle, health, per-client audit). No cross-agent discovery — retired ([ADR-003](../backend/adr/003-singular-resident-harness.md)).
- Per-agent quotas, rate limits, audit log surfaced to admins.
- BYOA settings UI (per workspace).

### Theme D — ~~A2A (Agent ↔ Agent) Fabric~~ (RETIRED — [ADR-003](../backend/adr/003-singular-resident-harness.md))

**Retired.** Under the singular-resident-harness direction, there is no
agent-to-agent fabric inside Integral. External agents coordinate *through the
shared substrate* via the MCP surface (Theme C), not through an inter-agent
protocol. The resident is one faceted mind, not an orchestrator of a fleet.
The former scope (uplink discovery, resident-as-orchestrator routing,
`a2a.delegate` audit trail, cross-agent permission propagation, A2A dashboards)
is dropped. Capacity that Theme D held is redirected to the defining bets —
resident **proactivity + memory** and **knowledge reach** — pulled forward per
ADR-003.

### Theme E — Knowledge Substrate Deepening

**Outcome:** The graph becomes genuinely *queryable* by agents, not just *readable*. Knowledge primitives that don't fit the productivity primitives get first-class node types. Retrieval combines deterministic graph traversal with semantic search.

**Scope includes:**
- Hybrid retrieval — graph traversal + semantic index over Markdown entry bodies and content profile manifests (ARCHITECTURE.md §22.1).
- Built-in knowledge EntryTypes: Decision, Risk, Stakeholder, Incident, Observation, Glossary Term (§22.10) — opt-in package, used by connectors and resident AI.
- Agent memory + working context (§22.4) — sticky context per conversation, scoped per workspace.
- Knowledge-graph querying tool surface for agents (§22.11) — `integral_query_graph`, `integral_traverse_relations`.

### Theme F — Foundation Hardening + GTM

**Outcome:** Integral graduates from pre-1.0 to a billable, deployable product. The bedrock — performance, real-time, audit, schema evolution, mobile, admin, billing — supports an outside customer.

**Scope includes:**
- Event stream / change feed (§22.6) — replaces polling; foundation for real-time UI and audit.
- Unified policy surface (§22.5) — single readable view of every grant for any subject.
- Schema evolution + migrations maturity (§22.8) — multi-step migrations, profile upgrade flows.
- Mobile responsive parity (no native app).
- Admin tier: org settings, member pool management, audit log surfacing, usage dashboards.
- On-prem / self-hosted deployment path.
- Pricing, billing, plan tiers.
- Public API documentation site.

---

## 4.0 Milestone Sequencing

Work is sequenced into eight numbered milestones. Each milestone is a roadmap-level outcome with explicit exit criteria; each decomposes into GSD phases via `/gsd-roadmapper` (internal agent workstreams — gitignored, not part of published repo docs). The orchestrator dispatches one milestone at a time. Phases within a milestone are mostly serial; the orchestrator can split independent phases into parallel worktrees via `gsd-workstreams` when their dependencies are disjoint.

Wall-clock estimates assume ~2–4 phases/week through plan → execute → verify → review. They are *upper bounds for orchestration planning*, not commitments. A milestone is "done" when its exit criteria pass `/gsd-verify-work` and `/gsd-audit-milestone`, regardless of calendar.

### M1 — "AI surface, hardened ground, proserve-deliverable" (~7–9 weeks)

**Headline:** Make the resident AI legible. Stabilize what we have. Make the substrate proserve-deliverable — methodology productized, LMS-on-Integral, multi-tenant hardened, governance visible.

This is the most expanded milestone vs v1.1, because the proserve practice cannot wait for an M5-class polish to deliver real engagements. Everything here is engagement-blocking.

**A. Resident AI surface**
- **A1: Conversational config UI** — chat-driven creation/edit of Views, EntryTypes, Tags, Profiles. Behind every form there's a chat affordance; behind every chat there's a diff preview. Backend already supports it via the agent contract — this is the frontend surface.
- **A2: Resident AI baseline** — agent inbox in the frontend, persistent conversation per workspace, substrate-aware prompting (every turn consults `integral_describe_substrate`).

**M. Methodology productization (proserve-aligned, derived from manifest 01-strategy)**
- **M1a: Three-Question Filter as a native workflow** — built-in flow that walks a user (or agent) through the filter on any candidate use case, stored as a structured artifact in the workspace.
- **M1b: Decision Record + Refusal Record as built-in EntryTypes** — first-class types with required fields, audit history, and query views. Maps to `08-governance/decision-record-template.md` and `08-governance/risk-register-template.md` in the manifest.
- **M1c: Three-Horizon dashboard as a built-in View** — composable view showing engagement progress across H1 / H2 / H3 with current gates, recent decisions, refusal count, KPI snapshot.
- **M1d: Use-Case Canvas EntryType** — maps to `15-templates/use-case-canvas.md`. Native capture for workshop output.
- **M1e: First sector overlay — development-bank / public-sector** — content profile package matching IDB-class engagement. Becomes the reference overlay franchise partners study.

**T. Tenant + governance hardening (engagement-blocking)**
- **T1: Multi-tenant workspace hardening** — strict workspace isolation across `X-Integral-Scope`, per-tenant DB partitioning option, per-tenant secret scoping, deletion → backup → restore tested end-to-end.
- **T2: Governance dashboard** — per-workspace surface showing access grants, agent uplinks, audit log highlights, refusal log, compliance posture. Maps to `08-governance/audit-checklist.md`.
- **T3: Audit log export** — CSV / JSON streaming export of audit events per workspace, scoped by date range. Compliance prerequisite.

**L. LMS-on-Integral mode**
- **L1: LMS content profile package** — a content profile defining `Module`, `Lab`, `Capstone`, `Enrollment`, `Submission`, `Certification` as EntryTypes with the right views. Maps to `03-lms/lms-architecture.md` and the role tracks.
- **L2: Learner experience surface** — minimum viable learner UI: enroll, consume module, submit lab, track progress, see certification. Lives inside Integral as a content-profile-driven experience, not a separate app.
- **L3: Author / facilitator surface** — author modules and labs as ContentProfile-typed entries; review submissions; issue certifications. Same workspace, role-gated.

**F. Foundation hardening**
- **F1: Foundation hardening** — workspace scope coverage audit; audit log surfacing; perf review; chunked-upload polish; observability baseline (metrics + structured logs); `../INVARIANTS.md` authored and CI-enforced where possible; internal GSD artifact debt cleanup.

**Exit criteria:**
- A net-new user can describe their domain in chat and have a working space + tracks + profile + views in under 5 minutes.
- Every existing endpoint enforces `X-Integral-Scope` with test coverage.
- A workshop-output use case can be captured as a Use-Case Canvas entry, run through the Three-Question Filter natively, and produce a decision record + (if applicable) refusal record in under 10 minutes.
- A workspace admin can see the Three-Horizon dashboard, governance dashboard, and audit log export from the UI.
- The LMS package can be applied to a new workspace, populated with one module + one lab, and an enrolled learner can complete and submit — end-to-end on Integral.
- `../INVARIANTS.md` exists and is referenced by the plan-checker on every substrate-touching phase.

### M2 — "Mirror model online" (~8–10 weeks)

*Sector overlay add: one additional vertical content profile package (financial services or healthcare — picked from active engagement pipeline).*



**Headline:** Connector framework lands. First hero integrations prove the mirror model.

- **B1: Connector framework** — `Connector` node type, sync engine, auth handshake, provenance metadata, incremental + backfill paths. Connectors compose with Content Profiles (each ships its own profile package).
- **B2: Jira connector** — issues, projects, sprints, comments → Tracks + Entries with relation fields. Two-way write deferred to M3.
- **B2: Gmail connector** — threads + messages → Entries under a "Communications" track. Entity-extraction sidecar (contacts, decisions) into knowledge entry types (stub).
- **A3: Resident AI uses connectors** — when an agent answers a question, it knows about mirrored Jira/Gmail data through the same graph traversal as native entries.

**Exit criteria:** A design partner can connect their Jira instance, see issues as Integral entries within 10 minutes of authorization, and have the resident AI summarize active work across Jira + Integral-native tracks in one query.

### M3 — "Connectors wave 2, BYOA Claude (multi-LLM-neutral by design)" (~10–12 weeks)

*Multi-LLM neutrality: Claude path is the first concrete BYOA, but the underlying uplink contract is vendor-neutral from day one. No Anthropic-only paths in the substrate. Brand-defense per STRATEGIC_POSITION §5.*



**Headline:** Breadth on connectors. First-class BYOA on the model the team uses most.

- **B3: Google Drive connector** — documents + folders → Entries; full-text indexed (precursor to E1).
- **B3: QuickBooks connector** — invoices, expenses, customers, vendors → typed entries under Finance.
- **B3: Slack connector** — channels + threads → Entries under Communications (mirrors only configured channels; never private DMs without explicit consent).
- **C1: Native Claude BYOA path** — Claude Projects connected to an Integral workspace via MCP. Claude skills can call Integral tools through the uplink registry. Per-user OAuth + per-workspace policy.
- **B5: Two-way write for Jira** (post-mirror feedback loop — agent creates a Jira issue from an Integral entry, with provenance link back).

**Exit criteria:** A team using Jira + QuickBooks + Slack can attach their Claude Project and have it answer "what blocked us this week?" by querying mirrored data across all three plus native entries. Audit trail shows every Claude call.

### M4 — "MCP-first API + OpenAI BYOA + registry" (~8–10 weeks)

**Headline:** External agents become first-class. The API contract becomes the agent contract.

- **C2: MCP-first API surface** (§22.9) — every CRUD endpoint, every list endpoint, every aggregation endpoint exposable as an MCP tool. REST endpoints become the human surface; MCP tools become the agent surface; both go through the same `resolve_role` + `X-Integral-Scope` checks.
- **C2: Native OpenAI BYOA path** — custom GPTs reading via Integral MCP, OpenAI routines invocable from inside Integral (mapped through the uplink registry).
- **C3: MCP connection management** — health pings, per-client quotas, rate limits, full audit per invocation surfaced to admins. No cross-agent discovery/capability declarations (retired — [ADR-003](../backend/adr/003-singular-resident-harness.md)).
- **C4: BYOA settings UI** — workspace admin can list registered agents, scope them, revoke them, view per-agent audit.

**Exit criteria:** A workspace admin can register a custom GPT and a Claude Project alongside the resident AI, scope each to a subset of the workspace, and see a unified audit log of every action any of them took. No agent has access the equivalent human user doesn't.

### M5 — "Retrieval, knowledge entry types, franchise enablement" (~10–12 weeks)

> **Resequenced per [ADR-003](../backend/adr/003-singular-resident-harness.md).** A2A is retired; the defining bets — resident **proactivity + memory** (formerly M6 E2) and **knowledge reach** (retrieval + tool parity) — are pulled forward ahead of external MCP polish and marketplace. Adjust the milestone body below accordingly during the next planning pass; the strike-throughs mark retired scope.

*Franchise enablement (proserve scaling): white-label / partner-mode UI, partner audit dashboard, content-pack distribution from HQ instance → partner instances (sector overlays, methodology updates), per-partner branded LMS. Maps to manifest `13-franchise/franchise-tooling.md` and `13-franchise/quality-standards.md`. Unlocks partner conversations at scale.*



**Headline:** Retrieval becomes semantic. Knowledge nodes go first-class. The resident reaches everything a human can.

- **B4: HRIS connector** (BambooHR, Rippling) — employees, roles, departments → User profiles + a People track.
- **B4: CRM connector** (HubSpot, Salesforce) — contacts, deals, accounts → Entries with relation fields to Communications.
- **B4: Calendar connector** (Google, Outlook) — meetings → Entries with attendees as relation field.
- ~~**D1: A2A discovery**~~ — **RETIRED** ([ADR-003](../backend/adr/003-singular-resident-harness.md)); external agents reach the substrate via MCP, no cross-agent routing.
- **E1: Hybrid retrieval** (§22.1) — semantic index over entry Markdown + content profile manifests; graph traversal stays primary; semantic results re-ranked by graph proximity.
- **E1b: Tool-parity closure** — close the gap-status entries in `tool_manifest.yaml` so the resident reaches every capability humans reach through the UI (parity matrix drives priority). Per [RESIDENT_HARNESS.md §6](RESIDENT_HARNESS.md).
- **F1: jvspatial-convention remediation** (Phase 6 Plan 06-05) — drift unwind: migrate 20 files (`backend/app/agentive/api/*` + 7 `backend/app/api/*` + `services/mcp_adapter.py`) from raw FastAPI patterns (`APIRouter`, `@router.<method>`, `HTTPException`, inline Pydantic) to jvspatial canonical (`@endpoint`, `JVSpatialAPIException` subclasses, schemas in `backend/app/schemas/{agentive,api}/`). Lands substrate invariants I-CONV-01..03; drains `.ci/jvspatial_drift_allowlist.txt` to zero; converts the pre-commit `jvspatial-drift-guard` hook from "allowlist-tolerated" to "enforcing tripwire." Mechanical convention-conformance; no behavioral change. Per root `AGENTS.md` § jvspatial Object-Spatial Contract.

**Exit criteria:** The resident handed "synthesize Q3 customer feedback" retrieves adjacent Slack threads and CRM deal context via hybrid semantic + graph search (all permission-filtered), and produces a Decision entry with relation fields to every cited source — reaching every needed capability through its own tool surface, no UI-only gaps. End-to-end action is auditable. **M5-HARDEN-1:** zero raw FastAPI patterns in `backend/app/` outside `main.py`; verified by `.ci/jvspatial_drift_check.sh` + `pytest backend/tests/test_jvspatial_convention_compliance.py`; I-CONV-01..03 enforced for all future phases.

### M6 — "Proactivity + memory + built-in knowledge nodes" (~8–10 weeks)

**Headline:** The resident works between sessions and remembers.

- ~~**D2: Resident AI as orchestrator**~~ — **RETIRED** ([ADR-003](../backend/adr/003-singular-resident-harness.md)); there is no fleet to orchestrate. Replaced by the proactive-loop items below (routines, schedules, agent-initiated turns).
- **D2′: Resident proactivity** — RoutineTask scheduler completion, `default_schedules` dispatch, agent-initiated turns in product use; the resident carries granted work forward on a clock/event trigger, every write staged. Per [RESIDENT_HARNESS.md §5](RESIDENT_HARNESS.md). *(Candidate to pull into M5 per ADR-003.)*
- **E2: Built-in knowledge EntryTypes** (§22.10) — Decision, Risk, Stakeholder, Incident, Observation, Glossary Term as opt-in package. Connectors populate them where appropriate (Jira incident → Incident node).
- **E2: Resident memory + working context** (§22.4) — scratch track + promotion (I-SCRATCH-01..05), per-conversation sticky state, per-workspace memory namespace, never crosses workspace boundaries. *(Candidate to pull into M5 per ADR-003.)*
- **F2: Schema evolution surface** (early) — content profile package versioning + upgrade flows for live workspaces.

**Exit criteria:** The resident remembers prior turns within a workspace conversation, runs a granted routine on schedule that drafts and stages work between sessions, promotes a polished scratch entry into a real track with provenance, and its output lands as typed Decision/Risk entries — every write blessed by a human.

### M7 — "Real-time + unified policy + knowledge-graph querying" (~8–10 weeks)

**Headline:** Replace polling. Make policy auditable. Give agents a real query surface.

- **F2: Event stream / change feed** (§22.6) — jvspatial change events streamed via WebSocket/SSE; clients subscribe per workspace. Polling becomes opt-in fallback.
- **F2: Unified policy surface** (§22.5) — single endpoint and admin UI that, for any subject (user or agent), enumerates every grant in scope, across workspaces, with provenance.
- **E3: Knowledge-graph querying tool surface for agents** (§22.11) — `integral_query_graph`, `integral_traverse_relations`, `integral_find_path` — deterministic graph queries as first-class MCP tools, complementing hybrid retrieval.
- **F3: Schema evolution / migrations maturity** (§22.8) — multi-step migrations, automatic profile upgrade with conflict review.

**Exit criteria:** A workspace owner can ask "who can read this track?" and see a complete grant chain (direct, inherited, share-link, agent uplink) in one view. Real-time updates land in the UI without polling. Agents have a deterministic graph query surface for cases semantic search shouldn't answer.

### M8 — "Productize → v1.0" (~10–14 weeks)

**Headline:** v1.0 — billable, deployable, supported.

- **F3: Mobile responsive parity** — every workflow usable on phone. No native app this release.
- **F3: On-prem / self-hosted** — single-tenant deployment path, customer-managed DB, customer-managed object storage.
- **F4: Admin tier** — org settings, member pool management at scale, full audit log surface, usage dashboards.
- **F4: Pricing, billing, plan tiers** — Personal (free), Team, Organization, Enterprise. Billing wired to workspace + connector + agent counts.
- **F4: Public API documentation site** — REST + MCP + connector SDK docs.
- **F4: v1.0 GTM polish** — landing page, marketing site, support flows, status page.

**Exit criteria:** v1.0 ships. Paying organizations can self-serve onboard via Team tier. Enterprise tier has a designed on-prem path. Public docs are complete.

---

## 5.0 Cross-Cutting Concerns

These don't fit neatly in one milestone — they thread the entire roadmap.

### 5.1 Design Partner Program (orchestrator-owned)

Claude does not recruit design partners. The orchestrator owns this track. Suggested cadence: 3–5 partners by end of M1; 8–10 by end of M3; 15–20 by end of M5. Every connector picked in M2+ should have at least 2 design partners using that source system before code starts (their schema is the test data). BYOA paths should have at least 2 partners actively running agents before the code is considered shipped.

### 5.2 Audit + Provenance Discipline

Audit and provenance are designed once in M1 (theme F1) and applied to every connector and every agent action thereafter. Every node carries `source` provenance; every state change emits a change event; every agent call records caller, target, scope, outcome. Every GSD phase that touches a write path includes provenance + audit coverage as plan-checker gates.

### 5.3 Security + Compliance

- **M1:** Threat-model sweep via `/gsd-secure-phase` over existing surfaces; secrets-management baseline; `security-review` skill applied to every phase that touches auth, sharing, or workspace scope.
- **M3:** SOC 2 readiness review (orchestrator engages auditor; Claude scaffolds policy docs + evidence collection).
- **M3 ↔ M4:** SOC 2 Type 1 (BYOA goes live → external systems hold tokens).
- **M5:** SOC 2 Type 2 audit window opens; HIPAA-ready posture for HRIS connector partners requesting it.
- **M7:** GDPR / CCPA tooling — export, delete, retention policy per content profile.
- **Every milestone:** `/gsd-code-review` and `/gsd-secure-phase` run on phases that touch sharing, BYOA, agent uplinks, connectors, or anything reachable from an unauthenticated endpoint.

### 5.4 Documentation Cadence

- **After every milestone:** regenerate the codebase map via `gsd-map-codebase`; run `gsd-extract-learnings`; update AGENTS.md, PRD.md, ARCHITECTURE.md as needed; refresh ROADMAP.md "Starting Point" (§2.0).
- **After every phase that changes a contract:** `/gsd-docs-update` to keep documentation aligned (in lieu of a CI doc-drift checker for now).
- **Connector SDK docs** live alongside framework (M2).
- **BYOA + MCP integration guides** live alongside Theme C (M3+).
- **Roadmap revision** is the orchestrator's call; Claude flags candidates in the Revision Log section.

### 5.5 Content Profile Evolution

Manifest v1.2 may be needed by M4 once BYOA usage shapes agent-authored profiles in production. Triggers: (a) connectors needing manifest-scoped relation types, (b) agents needing safer destructive operations than the current patch DSL. Decision deferred to the M4 → M5 review boundary.

### 5.6 Performance Budgets

- **M1 baseline:** p95 < 300ms for list endpoints; p95 < 1s for content-profile draft/publish.
- **M5 with hybrid retrieval:** p95 < 500ms for retrieval; p99 < 2s.
- **M7 with event stream:** < 1s end-to-end propagation from change to subscribed client.
- Budgets are verified per phase via `/gsd-verify-work` perf checks. Regressions block phase complete.

### 5.7 Team Hygiene (AI-Engineering Pipeline Discipline)

Specific to the team-scaled execution model. These are non-negotiable across all pods.

- **One phase, one branch, atomic commits.** Phase manifest tracked per GSD phase (internal workstreams). `gsd-undo` available for surgical rollback on a per-phase basis.
- **Verification is non-skippable.** Every phase exits through `/gsd-verify-work` + `/gsd-code-review` + (where applicable) `/gsd-secure-phase`. Failed verification → new phase, not amended commit. Engineering lead enforces; Eldon spot-checks 1-in-5 substrate-touching phases.
- **Parallelism cap per engineer:** ≤ 2 active AI coding pipelines. Beyond that, code quality and review quality both degrade.
- **Mandatory teammate code review on substrate-touching PRs.** A second engineer (not the author) reviews substrate-touching changes before engineering lead final approval.
- **Architecture review at milestone close** (Eldon, ~4–6 hrs). Spot-check substrate invariants; force a refactor phase if drift detected.
- **`../INVARIANTS.md` is referenced by plan-checker.** Every plan for a substrate-touching phase explicitly enumerates which invariants apply and how the plan preserves them.
- **Decision Records for substrate-invariant changes.** Required. Written by the proposing engineer; signed by Eldon (as architect).
- **No silent scope creep.** Deviations from the plan logged per GSD phase; surface in milestone audit. Plans growing > 1.5x original scope get decomposed, not amended.
- **Cross-pod coordination at milestone planning, not standing meetings.** Pods publish their phase list to a shared milestone planning doc; conflicts surface there. After milestone kickoff, pods run async until close.
- **Context discipline carries from solo-builder model.** `gsd-pause-work` / `gsd-resume-work` per engineer per pipeline; `gsd-extract-learnings` per pod per milestone close; `gsd-map-codebase` regenerated per milestone close.
- **Brief-driven, not Slack-driven.** Eldon's milestone brief is the source of truth for "why + must-do + must-not + done-looks-like." Engineers refer back to it; engineering leads check phases against it.

---

## 6.0 Key Bets and Trade-offs

These are the choices the sequencing encodes. Each is reversible at milestone boundaries.

| Bet | Why | Risk if wrong |
|---|---|---|
| **Team-scaled AI-engineering pipeline (10 engineers × Claude Code), not solo or conventional team** | Engineering velocity multiplier vs equivalent-headcount conventional team. Eldon scales up to visionary/architect; engineering leads own per-phase quality. Eliminates context-cap of solo build; trades it for cross-team coherence risk. | Architectural drift across ~20 concurrent pipelines is the dominant failure mode. Mitigation: `../INVARIANTS.md`, per-milestone architecture review, 1-in-5 substrate-touching PR spot-check, Decision Records for substrate changes, parallelism cap of 2 pipelines/engineer with mandatory teammate review. See §5.7 + VISIONARY_PLAYBOOK §3. |
| **Proserve-led product priorities (M1 expanded for engagement-blocking work)** | The practice cannot wait for product polish — methodology productization, LMS-on-Integral, multi-tenant hardening, governance dashboard all gate active engagement delivery. Building them in M1 unlocks revenue while feature breadth catches up. | M1 scope is now 5x v1.1's M1. Mitigation: pod-parallel execution; explicit exit criteria; each item independently deliverable so partial M1 close is acceptable. |
| **Methodology productized *into Integral*** | Three-Question Filter, Decision Record, Refusal Record, Three-Horizon Dashboard built as native UX — not just framework PDFs. Frameworks are rippable; native product UX of the framework is not. Brand defense per STRATEGIC_POSITION §3.1. | If methodology UX is undercooked, looks gimmicky and the framework feels diminished. Mitigation: methodology-product UX reviewed by Eldon personally before ship; design partner UAT mandatory before M1 close. |
| **Multi-LLM neutrality from M3, never Anthropic-only** | Anthropic + Big-Consulting partnership is the existential threat (STRATEGIC_POSITION §5). Substrate neutrality is the hedge. | Slightly higher per-vendor adapter cost in BYOA paths. Mitigation: MCP-first contract minimizes per-vendor code. |
| **AI surface (M1) before integration breadth (M2+)** | The substrate USP must be visible *immediately* so users see the difference vs. Notion/ClickUp. Empty graphs are fine if the conversational layer makes the substrate tangible. | If conversational config is undercooked, integrations arrive into a UI users still treat as a static productivity tool. |
| **Mirror model (M2) over bridge/proxy** | Agents need *one* query surface. Proxying external systems live multiplies the integration tax we set out to eliminate. | Higher up-front cost per connector. Conflict resolution complexity. |
| **MCP-first API in M4, not M1** | Internal REST contract is still moving (pre-1.0). Locking it as the agent contract too early creates churn for BYOA users. | If BYOA demand outpaces M4 timing, we ship informal MCP wrappers in M3 that need rework. |
| **Resident proactivity + memory (M5/M6), not M1** | A proactive resident is only trustworthy once the substrate it acts on is populated and the staging loop is proven. Reactive cocreation ships first; scheduled/autonomous work follows. Retired A2A capacity redirected here. ([ADR-003](../backend/adr/003-singular-resident-harness.md).) | If early users expect an autonomous assistant on day one, the reactive-only M1 resident underwhelms. Mitigation: frame M1 resident as cocreation partner, not autonomous agent. |
| **No mobile-native, no on-prem until M8** | Responsive web carries demos and SMB/mid-market. Native and on-prem are enterprise signals that gate v1.0. | Lose deals to Notion mobile in early sales motion. |
| **Hybrid retrieval (M5), not M1** | Graph traversal answers most agent queries. Semantic index without a populated graph is a worse Notion AI. | Some early users want "ask anything" and bounce when graph queries miss novel phrasings. |
| **Pricing in M8, not earlier** | Pricing the substrate is hard without seeing how customers value it. Design-partner agreements carry early revenue / commitment. | Cash flow concerns; some design partners want a published price to commit. |

---

## 7.0 Team Playbook (Roadmap → Pods → GSD → Code)

This roadmap is the strategic layer. The execution layer is GSD phases (internal agent workstreams), distributed across pods. Eldon authors the milestone brief and runs architecture review; engineering leads run their pods; engineers run their pipelines.

### 7.1 Mapping

- One **roadmap milestone** (e.g., M2 "Mirror model online") maps to one **GSD milestone** (e.g., `M2-mirror-model-online`).
- One GSD milestone decomposes into 8–20 **phases** (more than v1.1 because pod-parallelism allows higher concurrency).
- Phases are grouped into **pod assignments** for the milestone (e.g. Pod A → phases 001-006 on Theme A; Pod B → phases 007-012 on Theme B).
- Each phase follows the standard GSD cycle: spec → discuss → plan → execute → verify → code-review → secure (if applicable) → complete.

### 7.2 Per-Milestone Loop

**Eldon (Visionary + Architect):**
1. **Author milestone brief** (one document; format in [VISIONARY_PLAYBOOK §6.1](../../../integral_manifest/00-master/VISIONARY_PLAYBOOK.md)). Includes why, must-do outcomes, constraints (invariants), done-looks-like (exit criteria), what-it-is-NOT (scope cut), open questions for the team.
2. **Approve pod assignments** drafted by engineering leads.
3. **Per phase touching substrate invariants:** review plan before execute. Sign Decision Records as needed.
4. **Spot-check 1-in-5 substrate-touching PRs.**
5. **Run milestone architecture review at close** (~4–6 hrs).
6. **Decide milestone close** via `/gsd-audit-milestone`.

**Engineering Lead(s):**
1. **Decompose milestone brief** into phase list via `/gsd-roadmapper`. Submit to Eldon for approval.
2. **Assign phases to engineers within pod.**
3. **Per phase:** verify plan quality (`/gsd-plan-phase` output), enforce verification gates, run code review, manage merge train.
4. **Surface architecture concerns to Eldon** as soon as detected.
5. **Run pod retrospective at milestone close.**

**Engineer (per phase):**
1. `/gsd-spec-phase` if scope is ambiguous.
2. `/gsd-discuss-phase` to gather context.
3. `/gsd-plan-phase` — plan-checker gate; engineering lead reviews; Eldon reviews if substrate-touching.
4. `/gsd-execute-phase` — implement via AI coding pipeline; atomic commits per phase manifest. Cap at 2 active pipelines per engineer.
5. `/gsd-verify-work` — UAT against the phase's exit criteria.
6. `/gsd-code-review` (and `--fix` if findings). Mandatory teammate review on substrate-touching phases.
7. `/gsd-secure-phase` for any phase touching auth, sharing, BYOA, connector tokens, or external surfaces.
8. `/gsd-add-tests` if test coverage was deferred during execute.
9. Merge.

**Closeout (engineering leads + Eldon):**
- `/gsd-complete-milestone` plus `/gsd-audit-milestone` to verify exit criteria genuinely passed.
- Regenerate the codebase map via `gsd-map-codebase`.
- Per-pod `gsd-extract-learnings`; aggregated for Eldon.
- Doc refresh phase across AGENTS.md / PRD.md / ARCHITECTURE.md / docs/platform/content-profile.md.
- Eldon updates STRATEGIC_POSITION.md if a tested assumption changed; updates ROADMAP.md "Starting Point" (§2.0); promotes next milestone.

### 7.3 Per-Phase Gates (non-negotiable)

No phase merges until:

- ✓ Execute commit landed on the phase branch.
- ✓ `/gsd-verify-work` reports exit criteria met.
- ✓ `/gsd-code-review` clean (or fixes applied in a follow-up phase).
- ✓ `/gsd-secure-phase` clean if phase is on the security perimeter (auth, sharing, BYOA, agent uplinks, connectors, public endpoints).
- ✓ Mandatory teammate review on substrate-touching changes.
- ✓ Tests cover the change (UAT criteria mapped to tests via `/gsd-add-tests` when not done inline).
- ✓ `DEVIATIONS.md` reviewed for any in-flight scope adjustments.
- ✓ Decision Record written if substrate invariants were touched.

A phase that cannot pass these gates is decomposed further or its plan revised — never a silent merge.

### 7.4 Pod Parallelism

The default model is **multiple pods running in parallel** within a milestone. Each pod owns a theme or sub-theme; phases within a pod execute mostly serially (with `gsd-workstreams` for further intra-pod parallelism on independent slices). Cross-pod conflicts surface at milestone planning and at the per-milestone architecture review.

Anti-pattern to avoid: cross-pod dependency chains that force serialization. If Pod B's phase 007 needs Pod A's phase 004 done first, restructure: either move 004 earlier in Pod A's order, or move 007 into Pod A. Cross-pod blocking dependencies are a milestone-planning failure.

### 7.5 Doc Refresh Phase

Every milestone ends with a "doc refresh" phase that updates AGENTS.md, PRD.md, ARCHITECTURE.md, and docs/platform/content-profile.md as needed. Don't skip — it's how the next milestone's discuss-phase starts from a clean factual baseline.

---

## 8.0 Risks and Watch Items

Team-scaled AI-engineering execution shifts the risk profile from "single-builder context constraints" to "cross-team architectural coherence + brief quality + visionary bandwidth." Verification rigor, scope discipline, and context management remain critical but apply now at pod scale.

| Risk | Surface | Mitigation |
|---|---|---|
| **Cross-pod architectural drift** | M2+ (multi-pod milestones) | `../INVARIANTS.md` referenced in every plan; per-milestone architecture review by Eldon; 1-in-5 substrate-touching PR spot-check; mandatory teammate review on substrate-touching PRs; Decision Records for invariant changes. |
| **Brief quality erosion** | Every milestone | Eldon owns the milestone brief end-to-end per [VISIONARY_PLAYBOOK §6](../../../integral_manifest/00-master/VISIONARY_PLAYBOOK.md). Engineering leads cannot start pod assignments until brief is approved. Thin briefs surface as engineer-question volume — track and act. |
| **Verification gate skipped** ("looks done, ship it") | Every milestone | §7.3 gates non-negotiable; engineering leads enforce; Eldon spot-checks. `/gsd-audit-milestone` re-runs verification at milestone close — slips surface there. |
| **Silent scope creep within a phase** | Every phase | `DEVIATIONS.md` per phase. Engineering lead reviews before complete. Plans growing > 1.5x original get decomposed, not amended. |
| **Engineer running > 2 parallel pipelines** | Continuous | Engineering leads enforce parallelism cap. Quality of both code and review degrades past 2 active pipelines per engineer. |
| **Engineering lead bandwidth crunch** | M2+ (as pod count grows) | One lead per ~5 engineers; hire ahead of need. Eldon spot-checks pod throughput weekly. If lead bandwidth saturates, slow pod intake before quality drops. |
| **Eldon visionary capacity saturated** | Continuous (key risk) | [VISIONARY_PLAYBOOK §10](../../../integral_manifest/00-master/VISIONARY_PLAYBOOK.md) — time architecture, decision delegation. Hire delivery ops lead by engagement #4 so practice doesn't draw on engineering time. Mandatory quarterly off-site. |
| **Plan drift from roadmap** | M3+ | Roadmap revision log (§11) is the accountability surface. Engineering leads flag candidates; Eldon promotes. |
| **Context drift across long milestones** | M2+ | `gsd-pause-work` / `gsd-resume-work` per engineer per pipeline. Per-phase manifests as source of truth, not session memory. `gsd-map-codebase` refreshed at each milestone close. |
| **Agent quality stalls product perception** | M1+ | Resident AI is built on introspection of the substrate; quality bounded by tool surface, not LLM smarts. Track agent task success rate per workspace; gate features behind acceptable thresholds. |
| **Connector maintenance tax compounds** | M2+ | Connector SDK + provider-specific test harness from B1. Each connector ships with provider mock fixtures. Connector deprecation policy from M3. |
| **BYOA fragmentation across LLM vendors** | M3+ | MCP as the contract minimizes per-vendor code. Native paths (Claude, OpenAI) are convenience surfaces over the MCP core, not parallel implementations. |
| **Schema drift between content profile manifests and DB** | M1+ | Atomic-swap publish + draft/diff lifecycle already shipped. Schema evolution maturity (M7) hardens upgrade paths for live workspaces. |
| **Permission complexity across resident facets + external MCP agents** | M5+ | Unified policy surface (M7) gives admins a readable grant chain. Facets and external agents only ever *narrow* (fail-closed, I-AUTH-02): no principal grants another rights it doesn't itself hold. A2A propagation is moot — retired ([ADR-003](../backend/adr/003-singular-resident-harness.md)). |
| **Real-time backpressure on event stream** | M7 | Per-workspace event budget; lossy fallback to polling for cold clients. Designed alongside event stream implementation. |
| **External-dependency blockers** (OAuth approvals, Stripe, SOC 2 auditor) | M3+ | Orchestrator-owned track. Block-aware scheduling: connector / BYOA phases that need vendor approval start the approval request 2–4 weeks ahead. |
| **Competitive pressure pulls plan forward** | Continuous | Milestone boundaries are review gates. A theme can pull forward by 1 milestone at most without a roadmap revision; further compression requires explicit ROADMAP.md update logged in §11. |
| **Orchestrator bandwidth crunch** | Continuous | When orchestrator review backlog grows, slow phase intake before quality suffers. Better to ship M2 well than start M3 early. |

---

## 9.0 Open Questions

These need orchestrator decisions before the milestones they affect. Claude can scaffold options; the call is the orchestrator's.

- **M3:** Which CRM ships first — HubSpot (broader SMB) or Salesforce (enterprise gate)? Need a design partner per option to decide.
- **M4:** Does MCP-first API replace REST internally, or sit alongside? Affects frontend coupling.
- **M5:** Built-in knowledge EntryTypes — ship as an installable package or wire into the platform-default seed? Affects existing workspace migration.
- **M6:** Agent memory storage — a new `AgentMemory` node type or a property on `ChatThread`? Affects audit and TTL semantics.
- **M8:** On-prem deployment — single binary or container bundle? Affects upgrade story for self-hosted customers.

---

## 10.0 Revision Cadence

This roadmap is reviewed at every **milestone boundary** (M1 close, M2 close, …). Each review:
- Confirms or revises the next milestone's scope.
- Updates the "Starting Point" section (§2.0) to reflect what shipped.
- Logs every roadmap deviation (and its reason) in §11 Revision Log.
- Re-runs `gsd-map-codebase` if the internal codebase map is more than one milestone old.
- The orchestrator promotes revisions; Claude flags candidates.

---

## Appendix A — Vision-Aligned Directions Mapping

How the architecture's "Vision-Aligned Architectural Directions" (ARCHITECTURE.md §22) map into this roadmap:

| §22 Direction | Theme | Milestone |
|---|---|---|
| §22.1 Hybrid Retrieval | E1 | M5 |
| §22.2 Knowledge Provenance | B1 | M2 |
| §22.3 Connector Framework (Mirror Model) | B1 | M2 |
| §22.4 Agent Memory & Working Context | E2 | M6 |
| §22.5 Unified Policy Surface | F2 | M7 |
| §22.6 Event Stream / Change Feed | F2 | M7 |
| ~~§22.7 Agent ↔ Agent Fabric~~ | — | RETIRED ([ADR-003](../backend/adr/003-singular-resident-harness.md)) |
| §22.8 Schema Evolution & Migration | F3 | M7 (early surface in M6) |
| §22.9 MCP-First API Surface | C2 | M4 |
| §22.10 Built-in Knowledge EntryTypes | E2 | M6 |
| §22.11 Knowledge-Graph Querying for Agents | E3 | M7 |
| §22.12 Architecture Open Questions | Continuous | Reviewed at each milestone close |

---

## Code review backlog (tracked, not scheduled)

Items surfaced by the 2026 code review remediation; promote into milestones at close:

| Item | Location | Notes |
|------|----------|-------|
| Gap MCP tools | `backend/app/agentive/tool_manifest.yaml` (`status: gap`, ~1 tool: `integral_bulk_move_entries`; ~99 `existing`) | Parity nearly closed; bulk move deferred |
| Wave E entry-relations tests | `backend/tests/test_endpoint_entry_relations.py` | Module skipped |
| Chunked upload enablement | `CHUNKED_UPLOAD_ENABLED` default off | Backend returns 503 until enabled |
| Plugin signature verification | `content_profile_plugins.py` | v1 stub |
| Agentive Walkers | `backend/app/agentive/` | Multi-hop still procedural |

---

## Appendix B — Glossary

- **AI product arbitrage:** The strategic position of capturing value at the seam between the rapidly-evolving AI tooling ecosystem and organizations' fragmented domain knowledge — by being the schema-coherent, permission-coherent substrate any compliant agent can plug into.
- **Mirror model:** Connector pattern in which external knowledge is ingested as first-class nodes in the Integral graph with provenance metadata, rather than proxied via live queries to the external system. Contrast with *bridge model*.
- **BYOA (Bring Your Own Agent):** Any MCP/Skills-compatible agent (Claude Projects, custom GPTs, internal copilots, third-party agents) connected to Integral through its **MCP surface**. BYOA agents read and write through the same access model, staging, and audit trail as humans. (Scope is MCP-only per [BYOA.md](BYOA.md) / [ADR-003](../backend/adr/003-singular-resident-harness.md).)
- **A2A fabric:** *(Retired — [ADR-003](../backend/adr/003-singular-resident-harness.md).)* Formerly agent-to-agent discovery and call routing. Superseded: external agents coordinate through the shared substrate via MCP, not an inter-agent protocol.
- **Resident harness:** Integral's singular resident agent, faceted by principal (personal, org-facing, system). The primary surface; the UI is a projection of the same substrate. Full spec: [RESIDENT_HARNESS.md](RESIDENT_HARNESS.md).
- **GSD milestone / phase:** Execution-layer artifacts from internal agent workstreams (gitignored), derived from this roadmap. See the `gsd-*` skill family.

---

## 11.0 Revision Log

| Date | Author | Change |
|---|---|---|
| 2026-05-16 | Eldon Marks | v1.0 — Initial roadmap. Aligned to CONCEPT v5.0, PRD v5.0, ARCHITECTURE v2.0. Starting point: Phase 5 sharing primitives + Content Profile Pillars 1–4 + scaffolded agentive layer. |
| 2026-05-16 | Eldon Marks | v1.1 — Adapted for Claude-builder / human-orchestrator execution model. Added §1.1 Execution Model. Reframed §3 themes as "areas of work" (not parallel workstreams). Replaced quarterly grid (Q1–Q8) with milestone grid (M1–M8). Added §5.7 Claude-Builder Hygiene. Rewrote §7 as Orchestrator Playbook (per-milestone loop + per-phase gates + worktree parallelism). Risks updated: foreground verification/scope/context discipline, deprecate team-coordination concerns. Open questions and Appendix A relabeled Q→M. Revision Log promoted to §11. |
| 2026-05-16 | Eldon Marks | v1.2 — Adapted for team-scaled AI-engineering pipeline (10 engineers + hire capacity) and Eldon-as-Visionary-Architect (not phase orchestrator). Aligned with proserve practice per `integral_manifest/00-master/STRATEGIC_POSITION.md` v1.0 and `VISIONARY_PLAYBOOK.md` v1.0. **Major changes:** (a) Rewrote §1.1 for multi-pod execution; added §1.2 Decision Rights. (b) M1 expanded ~5x to include methodology productization (Three-Question Filter, Decision/Refusal Records as EntryTypes, Three-Horizon Dashboard, Use-Case Canvas, first sector overlay), multi-tenant + governance hardening, LMS-on-Integral mode. (c) Wall-clock estimates across M1–M8 compressed ~40% reflecting pod parallelism. (d) Added franchise enablement to M5 (white-label, partner audit, content-pack sync). (e) Multi-LLM neutrality elevated to M3 from M4 implicit. (f) §5.7 rebranded Team Hygiene with parallelism caps, mandatory teammate review, per-milestone architecture review. (g) §6.0 bets updated: team-scaled execution, proserve-led priorities, methodology-productized, multi-LLM-neutral. (h) §7 rewritten as Team Playbook with role-distinct loops (Eldon / Engineering Lead / Engineer / Closeout). (i) §8 risks reshaped around cross-pod drift, brief quality, engineering lead bandwidth, visionary capacity. |
| 2026-08-02 | Eldon Marks | Wave 6 doc rebaseline — §2.0 Starting Point refreshed: hybrid retrieval (`POST /api/retrieve`), policy engine, connector framework + Gmail/QB/GitHub, scratch memory, and tool-manifest parity (~99 existing / ~1 gap) marked partially or mostly landed; M1 methodology/LMS exit criteria noted as potentially still open. Code-review backlog tool counts corrected. Companion updates in ARCHITECTURE §22 gap language, RESIDENT_HARNESS path fix, BYOA live-path clarification, INVARIANTS containment chain (WorkspaceApp), invitation role comment alignment. |
