# Integral: Product Concept

**Version:** 5.0
**Date:** 2026-05-06
**Owner:** Eldon Marks

---

## 1. What Is Integral

Integral is an **AI-native knowledge platform** — a singular, conformable substrate that captures, organizes, and exposes all the domain knowledge an individual, team, or organization needs in order to operate atop an AI-first methodology.

It is built on a simple premise: **AI agents are only as effective as the knowledge they can reach.** Today that knowledge is fragmented across docs, drives, ticket systems, CRMs, chat threads, spreadsheets, and operators' heads. Each fragment lives behind a different schema, a different permission model, and a different API. Agents end up doing brittle integration work instead of useful work, and humans end up paying the integration tax for them.

Integral collapses that fragmentation into one substrate. It provides:

- **A unified knowledge graph** — all domain knowledge captured as graph-native, typed, taggable, queryable, permissioned nodes.
- **A flexible, conformable schema layer** — Content Profiles let humans and agents shape the knowledge model to match the domain, not the other way around.
- **A coordination surface** — tracking, contributions, and integrations from external systems happen on the same graph, under the same access policy.
- **A singular resident harness** — one Claude-like resident agent per deployment, faceted by principal (personal, org-facing, system); it reads and writes through the same APIs and the same permission checks as humans. External agents connect optionally through Integral's MCP surface under the same gates. (See [RESIDENT_HARNESS.md](RESIDENT_HARNESS.md) and [ADR-003](../backend/adr/003-singular-resident-harness.md).)
- **Collaboration as equals** — human-to-human and human-to-resident collaboration both operate over the same graph with the same primitives; external agents reach that same substrate via MCP.

In short: Integral is the substrate that makes AI-native operation actually possible, at the level of one person or one enterprise.

### What This Substrate Enables

Because the same five primitives (Space, Track, EntryType, Tag, View — see §4) compose any internal operating tool, Integral is positioned as the layer beneath them all. A CRM is a Space of Contact and Account Tracks with a board view. A project tracker is a Track of Task entries with a kanban view. An evaluation system is a set of related Tracks with composite Indicator types. A wiki is a long-form rendering of the same entries. The platform is not competing with one of these tools — it is the substrate on which all of them can be re-expressed.

This unlocks three authoring paths at once. End users can describe the work in plain language and have the resident draft the ContentProfile changes; engineers can extend the substrate in code; partners can ship reusable bundles. For the agentive era, the destination is straightforward: for the operations a company runs to keep its business going, Integral is positioned as the only suite it needs. SaaS subscriptions retire as Integral surfaces stand up. Integrations stop being one-off pipes and become connectors registered on the same gate. Traditional human user interfaces are preserved — humans see tables, boards, calendars, and feeds over the same data agents reason about — so the team's experience doesn't change shape underneath them.

---

## 2. The Problem

AI-native methodology fails on a fundamental impediment: **AI agents have no canonical place to read from or write to.** Every team adopting "AI-first" hits the same wall.

| Impediment | Concrete Symptom |
|------------|------------------|
| **Fragmented knowledge** | Domain knowledge lives in N silos with N integration patterns. Agents cannot reason holistically without bespoke RAG plumbing per use case. |
| **Inconsistent access policy** | What a human can see in tool A is not what an agent should be allowed to act on through tool B. There is no unified policy surface. |
| **Unstructured coordination** | Slack/email/docs are text blobs. There is no structured graph of who is responsible for what, what depends on what, what changed when. |
| **Ad-hoc agent memory** | Agents cannot recall stable facts about an organization across sessions without one-off vector stores per project. |
| **Brittle external integrations** | Each new external system requires a custom connector, schema mapping, sync logic, and access pattern — multiplied per agent that needs it. |
| **No canonical agent surface** | External agents and systems have no single, permissioned place to read and write an organization's knowledge — every integration reinvents its own. |

**Integral exists to solve this.** One graph. One access model. One schema layer. One coordination surface. One agentive contract — for both humans and agents, internal and external.

---

## 3. Modes of Collaboration

Integral treats collaboration vectors as first-class peers. The graph is the medium; humans, the resident harness, and external agents are equal participants under the same permission model.

| Vector | Example | Substrate Provided |
|--------|---------|--------------------|
| **Human ↔ Human** | Founder shares a venture space with co-founders | Spaces, Tracks, viewer/editor roles, shared feeds |
| **Human ↔ Resident** | Operator asks the resident to summarize a track and propose next actions; resident reads scoped graph, stages proposals back as entries | One resident harness over the user's accessible subgraph; resident writes go through the same permission checks and staging as the user |
| **External agent ↔ Substrate** | A user's own Claude/Cursor/pipeline reads and writes the workspace through Integral's MCP surface | MCP server: same tool catalogue, same policy gates, same staging and audit trail as the resident |

Information sharing, contribution, and coordination across these vectors flow through the same primitives. There is no second-class citizen. There is no separate agent-to-agent fabric inside Integral — external agents coordinate *through the shared substrate*, not through a bespoke inter-agent protocol (see [ADR-003](../backend/adr/003-singular-resident-harness.md)).

---

## 4. The Recombinable Primitives

Integral's flexibility derives from five composable primitives. They double as **knowledge primitives** (typed nodes in a domain graph) and **productivity primitives** (containers for tracking and coordination work). Content Profiles compose them into cohesive domain models.

| Primitive | Role | What It Defines |
|-----------|------|-----------------|
| **Space** | Domain / context | Groups related Tracks; carries collaboration scope; may prescribe Track types via its attached Content Profile |
| **Track** | Focused container | Single-purpose container for Entries; owns exactly one Content Profile that defines its EntryTypes, Tags, and Views |
| **EntryType** | Knowledge schema | Blueprint for Entries — defines fields, validation, required tags, display rules. An EntryType is the schema for a class of facts (Contact, Decision, Incident, Observation, Task, etc.). |
| **Tag** | Cross-cutting classification | Scoped labels that cut across entity types; grouped into taxonomies; may constrain which EntryTypes they apply to |
| **View** | Presentation lens | Saved configuration for rendering Entries (Feed, Kanban, Table, Calendar, Gallery); defined per-Track |

These primitives compose naturally for both knowledge and productivity work:

- **Knowledge model** — a Track with EntryTypes for `Decision`, `Risk`, `Stakeholder`, with relation fields linking decisions to risks and risks to mitigation tracks = a **decision-and-risk register** queryable by agents and humans alike.
- **Coordination model** — a Track with EntryTypes for `Task` and `Milestone`, Tags for `Priority` and `Team`, and a Kanban View = a **project tracker**.
- **Operational model** — a Space with prescribed Tracks (`Contacts`, `Engagements`, `Renewals`) linked by cross-Track relations = a **CRM** that an agent can act on holistically.

The bet: the same five primitives that make a flexible productivity tool also make a flexible knowledge graph. Both are graphs of typed, related, taggable nodes.

---

## 5. Content Profiles: The Composable Specification

### 5.1 What a Content Profile Is

A Content Profile is a **declarative specification** that describes how the five primitives should be configured for a Space or Track. It is not a template that stamps — it is a **live, inspectable, modifiable specification** that drives runtime behavior. For agents, the Content Profile is the **published schema of the domain** they are operating on.

**Two scopes:**
- **Track-scoped** (`scope: track`): Defines EntryTypes, Tag taxonomy, Views, and defaults for a single Track.
- **Space-scoped** (`scope: space`): Defines prescribed Tracks (each with their own track-scoped spec), cross-Track relations, and space-level defaults.

### 5.2 The Profile Lifecycle

```
Author → Library → Apply → Customize → Evolve → (Share back)
```

1. **Author**: Create a Content Profile from scratch (human or AI), or derive one from an existing Track/Space.
2. **Library**: Publish to the shared library (platform, organization, or community scope).
3. **Apply**: Select a profile from the library and apply it to a new or existing Space/Track. The profile's specification is materialized into the attached Content Profile — creating EntryType, Tag, and View nodes.
4. **Customize**: After application, every element is mutable in-place. Add, remove, reorder, or modify EntryTypes, Tags, and Views. The attached profile tracks what was applied and what was customized.
5. **Evolve**: When a library profile is updated, optionally re-merge improvements. The system tracks provenance — which elements came from which library version — enabling intelligent merge of upstream changes with local customizations.
6. **Share back**: Optionally publish the customized profile back to the library (with attribution to the original).

### 5.3 AI Agent Authoring

AI agents can:
- **Author new profiles** from a natural-language description of the domain ("we run an incident-response process; entries should capture severity, on-call, root-cause, and post-mortem links").
- **Modify existing attached profiles** as the domain evolves (add an EntryType, adjust a View, refine the Tag taxonomy).
- **Recommend profiles** from the library based on observed user intent or knowledge already captured.
- **Evolve profiles** over time — suggest new fields based on usage patterns, recommend Views based on data shape.

Agent modifications go through the same permission system as human actions. Every change is auditable and reversible. This is what makes the schema layer **agent-authorable infrastructure**, not a static configuration concern.

### 5.4 In-Place Customization

After a Content Profile is applied, the user (or agent) can customize every aspect without breaking the profile's provenance:
- **Add** EntryTypes, Tags, or Views that weren't in the original profile.
- **Remove** elements that were applied (with awareness of dependencies).
- **Modify** field schemas, tag definitions, or view configurations.
- **Reorder** EntryTypes, tag groups, or views.
- **Set defaults** (default view, default EntryType, required tags).

The attached Content Profile's manifest is the **living specification** — always the source of truth for what the Track/Space contains, regardless of how those elements were originally authored.

---

## 6. The Resident Harness

**Conversation as primary interface.** Text Integral like you'd text a smart colleague. The web UI is a projection of the same substrate — an "organized view," no longer the only way to interact. Full specification: [RESIDENT_HARNESS.md](RESIDENT_HARNESS.md).

- **One singular resident harness**: Every deployment ships a single Claude-like resident agent that understands the accessible subgraph, permissions, and patterns. It operates over the same APIs the user has, with the same scope — not a black box.
- **Faceted by principal**: The one harness projects into facets — **personal** (the authenticated user), **org-facing** (external audiences over WhatsApp / email / OTP, hard-narrowed to authorized knowledge, no account required), and **system** (platform routines). Facets only ever narrow permissions; they are configuration on one mind, not separate agents.
- **External agents via MCP**: A user's own MCP/Skills-compatible agents (Claude, Cursor, custom pipelines) connect optionally through Integral's MCP surface — the same tool catalogue, policy gates, staging, and audit trail as the resident. There is no agent-to-agent fabric inside Integral; external agents coordinate through the shared substrate.
- **Proactive, with memory**: The resident is not answer-only. It carries work forward between sessions — routines, scheduled skills, scratch memory, and promotion of polished knowledge into real tracks (see [RESIDENT_HARNESS.md §5](RESIDENT_HARNESS.md)).
- **Operates through existing APIs**: No shadow CRUD, no bypassed access controls. If a human can't do it, the resident can't either. Every write stages for a human bless.
- **Profile-aware**: The resident applies, authors, and modifies Content Profiles when creating Tracks or Spaces — not limited to bare defaults. The schema layer is part of its toolkit.
- **Always-on ops layer**: The harness + agentive package load unconditionally in current code. Features are designed harness-first, with the UI as their projection (see [ADR-003](../backend/adr/003-singular-resident-harness.md)). Historical `AGENTIVE_ENABLED` substrate-only mode is not a live boot gate.

---

## 7. Core Philosophy

1. **Knowledge is the substrate, not the byproduct.** Productivity outputs (tasks, projects, plans) are projections of underlying knowledge. Capture the knowledge model first; coordination flows from it.

2. **Primitives, not presets.** The system exposes a small, composable set of building blocks rather than a fixed set of tools. Content Profiles are recipes that compose these blocks — not rigid templates.

3. **Conformability over rigidity.** The software conforms to how people and domains actually work. Content Profiles are starting points, not cages. Every element can be modified, reordered, extended, or removed after application.

4. **Humans and AI, collaborating as equals.** AI agents are first-class participants. They can author, modify, and evolve knowledge alongside humans. Every agent action is inspectable, reversible, and subject to the same permissions as a human action.

5. **One graph, one policy.** All knowledge and coordination state lives on one graph under one access model. There is no second knowledge store, no parallel permission system, no agent-only backdoor.

6. **Progressive disclosure.** Start with a feed. Discover views. Author a profile when you need one. Wire in an agent when the work warrants it. The system reveals power as needs grow.

7. **One track, one purpose.** A Track is a single-purpose container. Complexity is managed by creating multiple Tracks, grouped in Spaces — not by overloading one Track.

8. **Architecture is provisional.** The current implementation is a starting point. As the AI-native vision sharpens, the architecture should evolve to serve it (see *ARCHITECTURE.md → Vision-Aligned Directions*). No backwards-compatibility tax at the expense of coherence; the system is pre-1.0.

---

## 8. Target Users & Use Cases

| User | Need | How Integral Serves |
|------|------|---------------------|
| **AI-native team / company** | One source of truth that internal copilots, customer-facing agents, and humans all operate over | Unified knowledge graph + Content Profiles per domain + one resident harness (faceted) + MCP surface for external agents |
| **The Founder** | Single mission control across multiple ventures, queryable by an agent that already knows the whole picture | One Space per venture; prescribed Tracks per business function; cross-Track relations; per-user agent over the full accessible graph |
| **The Operator / PM** | Coordination tool that doesn't fight the workflow, with an agent that can plan, summarize, and act on it | Track-scoped profiles per workflow; agent reads tracks, drafts entries, proposes next actions |
| **The Solopreneur** | One platform that holds business knowledge and acts on it, instead of five subscriptions and a bolted-on AI | Personal Space; per-domain Tracks; personal agent operating over them through MCP |
| **The Researcher / Analyst** | Tag, connect, and visualize a knowledge graph; let an agent traverse it for synthesis | Rich taxonomy, relation fields across Tracks, gallery/table views, agent-driven graph traversal |
| **The Org running customer-facing agents** | Agents that answer customers without leaking internal data | Org-facing agents bound to scoped sub-graph; same permission model as human staff |

---

## 9. Competitive Positioning

| | Integral | Notion / Notion AI | Glean / Enterprise Search | Airtable | Vector-DB + RAG stacks |
|---|---|---|---|---|---|
| **Primary purpose** | AI-native knowledge substrate + coordination | Docs + lightweight DBs | Search across silos | Tabular DBs | Retrieval primitives |
| **Data model** | Graph with recombinable primitives + agent-authorable schemas | Blocks + databases | Index over external sources | Tables + views | Embeddings + metadata |
| **Source of truth** | Yes — knowledge lives in Integral | Sometimes; mixed with external | No — search-only over silos | Yes, but tabular only | No — index of sources |
| **Schema** | Content Profiles, AI-authorable, in-place customizable | Manual | Source-defined | Manual | None |
| **Agent integration** | First-class; resident harness reads/writes graph and authors profiles; external agents via MCP | Bolted-on chat features | Read-only retrieval | Limited | Plumbing only |
| **Human / resident / external-agent collaboration** | All three first-class over one substrate | H2H primarily; H2A surface-level | None | H2H | None |
| **External integrations** | Connectors mirror external systems into the graph | Per-block embeds | Read-only crawlers | Per-table syncs | Per-source ingestion |

**Integral's differentiator:** It is not a productivity tool with AI features, and it is not a retrieval index over other tools. It is the **knowledge substrate** an AI-native operation runs on. Everything else (UI, profiles, agents, integrations) is a projection of that substrate.

---

## 10. Guiding Principles for Implementation

1. **Knowledge-graph-first.** When adding capability, ask: "Does this enrich the graph or fragment it?" Capabilities that fragment the graph (separate stores, parallel permission models, agent-only bypasses) are rejected.

2. **Profile-first within the graph.** Once on the graph, ask: "Can this be expressed as a Content Profile specification?" If yes, it should be.

3. **Attached profiles are live specifications.** The manifest on an attached Content Profile is the source of truth. Materialized nodes (EntryType, Tag, View) are projections of this specification.

4. **Merge is additive, never destructive.** Applying a library profile adds to the attached profile. Customizations are preserved. Removing elements is explicit.

5. **AI agents are profile authors and graph citizens.** The MCP tool contract for creating Tracks and Spaces must include Content Profile application — agents must not create bare Tracks. Agents read and write the same graph as humans, scoped by the same permissions.

6. **External integrations mirror, do not bridge.** Connectors bring external knowledge into the Integral graph as first-class nodes (with provenance), rather than leaving it remote and proxying queries. The graph is the primary store; external systems are sources.

7. **The library grows organically.** Platform-seeded profiles provide starting points. Organization-private and community profiles fill vertical needs. Users can publish customizations back.

8. **No backward compatibility at the expense of coherence.** The system is pre-1.0. If the model needs to change to serve the vision, it changes. Existing data can be migrated.

---

## 11. Document Map

- **CONCEPT.md** (this document): Vision, problem, philosophy, primitives, profile lifecycle, agentive layer
- **ARCHITECTURE.md**: Technical architecture, data model, APIs, implementation details, vision-aligned architectural directions
- **PRD.md**: Product requirements, epics, acceptance criteria, success metrics
- **CLAUDE.md**: Developer quickstart, commands, conventions
- **docs/README.md**: Technical documentation hub (substrate, backend reference, ops)
- **docs/product/**: Product strategy docs (this directory)
- **docs/content-profiles/**: Content-profile substrate scaffolding (Pillars 1–4, agent contract, draft/publish)
