# Integral: Product Requirements Document (PRD)

**Document Version:** 5.0
**Date:** 2026-05-06
**Product Owner:** Eldon Marks
**Status:** Aligned with CONCEPT v5.0 & ARCHITECTURE v2.0

---

## 1.0 Document Purpose & Overview

This Product Requirements Document (PRD) defines the vision, scope, and detailed requirements for **Integral** — an **AI-native knowledge platform** that serves as a singular source of access to all the domain knowledge an individual, team, or organization needs to operate atop an AI-first methodology. Integral provides a flexible, conformable substrate for knowledge management that simultaneously facilitates tracking, contributions, integrations from external systems, and three-way collaboration: human-to-human, human-to-AI, and AI-to-AI. This PRD translates the strategic product concept (CONCEPT.md v5.0) into an actionable blueprint for design, development, and launch, and serves as the single source of truth for all stakeholders (product, engineering, design, QA, and go-to-market teams).

---

## 2.0 Product Vision & Positioning

### Vision Statement
To be the **knowledge substrate that AI-native organizations run on** — a singular, conformable graph of domain knowledge over which humans and AI agents collaborate, coordinate, and act under one access model.

### Problem Statement
AI-native methodology fails on a fundamental impediment: **AI agents have no canonical place to read from or write to.** Domain knowledge is fragmented across docs, drives, ticket systems, CRMs, chat threads, spreadsheets, and operators' heads — each behind a different schema, permission model, and API. Agents end up doing brittle integration work instead of useful work. There is no unified policy surface, no single permissioned surface for external agents, and no shared schema that humans and agents can both author. Integral exists to remove that impediment.

### Positioning Statement
For individuals, teams, and companies adopting an AI-first methodology, Integral is the **AI-native knowledge platform** that gives every agent and every human a single source of access to all their domain knowledge. Unlike productivity tools that bolt AI on top of fragmented data, retrieval stacks that index but do not own knowledge, or enterprise search that crawls without coordinating, Integral makes the graph itself the substrate: agents and humans read, write, schema, and coordinate over the same primitives, under the same permissions, through the same APIs. Productivity outputs (tasks, projects, plans) are projections of the underlying knowledge model — not the model itself.

### Core Philosophy
Integral operates on the principle that **knowledge is the substrate, not the byproduct**, and that AI agents must be first-class citizens of that substrate — not after-the-fact integrations against it. Software should conform to how people and domains actually work, structure should be agent-authorable, and access policy should be unified across humans and agents.

### Key Differentiators
- **One graph, one policy:** All knowledge and coordination state lives on a single graph under a single access model. No second knowledge store, no parallel permission system, no agent-only backdoor.
- **Recombinable primitives that double as knowledge primitives:** Five composable blocks (Space, Track, EntryType, Tag, View) serve as both productivity containers and a typed knowledge graph.
- **Operational Models as agent-authorable schemas:** Declarative, composable, AI-authorable specifications that drive runtime behavior. Not static templates — living specifications customizable in-place after application, and inspectable as the published schema of the domain agents operate on.
- **Collaboration as equals:** Human↔Human and Human↔Resident flow through the same primitives, with the same permissions, on the same graph; external agents reach that same substrate via MCP. (See [ADR-003](../backend/adr/003-singular-resident-harness.md).)
- **Singular resident harness:** One Claude-like resident agent per deployment, faceted by principal (personal, org-facing, system), operating over the graph through the same APIs and staging as humans. External MCP/Skills-compatible agents connect optionally through Integral's MCP surface under the same gates. Full spec: [RESIDENT_HARNESS.md](RESIDENT_HARNESS.md).
- **External systems mirror, not bridge:** Connectors ingest external knowledge as first-class nodes with provenance, rather than proxying remote queries — the graph is the primary store.
- **Google Docs–style sharing:** Spaces and Tracks shared with `viewer` / `editor` collaborators; ownership transfer by elevating a collaborator to `owner`. Sensitive work uses separate tracks or visibility — not per-entry ACLs.
- **Progressive disclosure:** Feed-based simple use; views, profile authoring, and agent wiring revealed as needs grow.

---

## 3.0 Target Users & Personas

Integral is built for individuals, teams, and companies operating atop an AI-first methodology, plus the agents that serve them.

| Persona | Core Job-to-be-Done | Key Pain Points Integral Solves |
| :------ | :------------------- | :----------------------------- |
| **The AI-Native Org** (founders, ops leads, CTOs of AI-first teams) | "We want our internal copilots, customer-facing agents, and humans to all operate over the same source of truth — without rebuilding it per agent." | No canonical knowledge store for agents. Inconsistent permissions across tools. No single permissioned surface for external agents. Integration tax multiplied per agent. |
| **Eldon, The Founder** | "I run several ventures and an AI division. I need one mission-control graph my agent can already reason over — across ventures, functions, and external systems." | Context fragmentation across companies and tools. No agent that sees the whole picture. Manually piping context into copilots per session. |
| **Alex, The Agile Operator / PM** | "I need a coordination tool that doesn't fight my workflow, plus an agent that can plan, summarize, and act on it." | Tool sprawl, context switching, AI assistants that can't see structured project state. |
| **Sam, The Solopreneur** | "I need one platform that holds my business knowledge and lets my personal agent act on it — instead of five SaaS subscriptions and a bolted-on chat." | Cost of multiple subscriptions, brittle linking between apps, agents that don't know my domain. |
| **Taylor, The Research Lead / Analyst** | "I need a typed, taggable knowledge graph my agent can traverse for synthesis — not a pile of docs." | Static data organization, poor collaborative knowledge graphs, no agent-traversable structure. |
| **The Org Running Customer-Facing Agents** | "We want our public-facing agent to answer customers using only authorized knowledge — without reinventing access control per channel." | No scoped sub-graph for public agents. Risk of leakage. No unified policy surface across human staff and agents. |
| **Trudy, The Coordinator** | "I'm planning my mother's 75th birthday with my siblings; I want a simple shared place plus an agent that can keep us aligned." | Chaos of group chats and emails. No centralized state. No agent that knows the whole plan. |

---

## 4.0 Core Product Principles

1. **Knowledge is the substrate, not the byproduct.** Productivity outputs (tasks, projects, plans) are projections of underlying knowledge. The graph is the primary artifact; everything else is a view on it.
2. **One graph, one policy.** All knowledge and coordination state lives on one graph under one access model. No second knowledge store, no parallel permission system, no agent-only backdoor.
3. **Primitives, not presets.** The system exposes composable building blocks rather than a fixed set of tools. Operational Models are recipes that compose these blocks — not rigid templates.
4. **One Track, One Purpose.** A Track represents a single, coherent idea, project, or activity. Complexity is managed by creating multiple discrete Tracks, grouped in Spaces.
5. **Conformability over rigidity.** The system adapts to the user's and the domain's mental model, not the reverse. Operational Models are starting points, not cages.
6. **Progressive disclosure.** Start simple (Feed), reveal power (Views, Profile authoring, agent wiring) as needed.
7. **Humans and AI, collaborating as equals.** AI agents are first-class participants. They read and write the same graph, through the same permission-checked APIs and Operational Model specifications, as human users.
8. **External systems mirror, not bridge.** Connectors bring external knowledge into the graph as first-class nodes with provenance, rather than proxying remote queries.
9. **Architecture is provisional.** Pre-1.0. If the model needs to change to better serve the AI-native vision, it changes — see ARCHITECTURE.md → Vision-Aligned Directions.
10. **Intelligent defaults, full control.** AI suggests; the user always decides and can override. Every agent change is auditable and reversible.

---

## 5.0 Key Features Overview

- **Unified Knowledge Graph:** All domain knowledge captured as graph-native, typed, taggable, queryable nodes — the single source of truth for both humans and agents.
- **Recombinable Primitives:** Five composable building blocks — **Space**, **Track**, **EntryType**, **Tag**, **View** — that Operational Models compose into any domain or productivity application.
- **Spaces & Track Architecture:** **Spaces** group related **Tracks** (personal or under an **Organization**). Collaborators invited to a space get access to all contained tracks by default; track owners may stack direct collaborators or refine access per track.
- **Entry System:** Typed entries (task, note, contact, decision, observation, etc.) with rich content, custom fields, relation fields, and tagging. Entry visibility is governed only by track (and inherited space) access — no per-entry ACLs.
- **Adaptive Views:** Feed (default), Kanban, Calendar, Table, Gallery — all real-time and synchronised.
- **Multi-Level Feeds:** Social-style, infinitely scrollable, filterable feeds at user-wide, space-wide, and track-local levels.
- **Google Docs–Style Collaboration:** Share spaces/tracks with `viewer` / `editor`; invite existing users or by email; transfer ownership by promoting a collaborator to `owner`.
- **Organizations:** Org admin maintains a member pool and assigns selective rights to create spaces/tracks under the org; org can host spaces and standalone tracks.
- **Operational Models as Living Specifications:** Every Space and every Track has exactly one attached `OperationalModel`, linked via `HAS_OPERATIONAL_MODEL`. The attached Operational Model's `manifest` is the source of truth — and the published schema agents reason against. After a library Operational Model is applied (merge), every element can be customized in-place (add, remove, reorder, modify EntryTypes, Tags, Views). The manifest updates on every customization, enabling provenance tracking and intelligent re-merge of upstream library updates. Library packages under `App` → `OperationalModels` are optional starting points that merge into attached Operational Models (library nodes stay unchanged; no live bind after merge).
- **Universal Tagging:** Tags apply across entities (organization, space, track, entry, view, profile). Tag hierarchy and `applies_to_entry_types` constraints are enforced.
- **AI-Authorable Operational Models:** AI agents can author new Operational Models from natural-language descriptions of a domain, modify existing attached Operational Models, and recommend profiles from the library. Agent modifications go through the same permission system as human actions.
- **Resident Harness (ops layer + pluggable coworker mind; always-on):** Integral augments an active harness binding (default embedded jvagent; Harness Switcher may select Echo for smoke/dev). Singular mind **per binding**, faceted by principal — **personal**, **org-facing** (WhatsApp/email/OTP — not fully productized), and **system**. Same permission-checked APIs and staging as humans. Full spec: [RESIDENT_HARNESS.md](RESIDENT_HARNESS.md).
- **MCP Surface for External Agents:** Any MCP/Skills-compatible agent (Claude, Cursor, custom pipelines) connects optionally through Integral's MCP server — same tool catalogue, policy gates, staging, and audit trail as the resident. There is no separate agent-to-agent fabric; external agents coordinate through the shared substrate ([ADR-003](../backend/adr/003-singular-resident-harness.md)).
- **External-System Connectors (mirror model):** Connectors ingest external knowledge as first-class nodes in the graph with provenance metadata, enabling agents to reason holistically without per-source RAG plumbing.
- **Unified Audit Trail:** Every change — human or agent — is auditable and reversible.
- **Headless API & YAML Definition:** Entry types and views defined via YAML (inspired by GRAV CMS); operational models described by a standard package schema (ARCHITECTURE.md §5.3); every API endpoint is intended to be exposable as an MCP tool.

---

## 6.0 Detailed Requirements & Specifications

### Epic 1: Spaces, Tracks & Core Architecture

- **Goal:** Spaces group tracks; tracks remain single-purpose; optional organization context.
- **User Story:** As a user, I can create a **Space** to group related tracks (e.g. per company or initiative) and create **Tracks** inside or outside a space, so my workspace stays organized and sharing scales.
  - **Requirements:**
    - Create a **Space** (individual or **under an Organization**): name, description, tracks linked to the space.
    - Create a **Track** with title, icon, description/purpose, and **visibility** (`private` | `organization` | `public` to authenticated users).
    - **Single‑Purpose Enforcement** for each track (UI and guidance).
    - **Attached profiles:** New **Spaces** and **Tracks** each get a **Default** **`OperationalModel`** (**`HAS_OPERATIONAL_MODEL`**). **Track-attached** profile holds **EntryType** / **Tag** / **View** subgraph (default type + **feed** view at create—**ARCHITECTURE.md** §3.4).
    - **Space track templates:** Space-attached Operational Model **may** define **multiple** **`DEFINES_TRACK_PROFILE`** **track-template** profiles for **new-track** flows in that space.
    - **Library (optional):** User **may** pick a **library** **`OperationalModel`**; **merge** **extends** the relevant **attached** **Default** per manifest **`scope: track`** or **`scope: space`** (**ARCHITECTURE.md** §5.3). **`attachedOperationalModelId`** (required), **`libraryMergeSourceId`** (optional provenance).
    - **Track templates / cloning:** Duplicate an existing track structure (**`USES_TEMPLATE`**)—distinct from **library** merge into attached Operational Models.

### Epic 2: Entry System & Multi-Level Feeds

- **Goal:** Capture content per track and surface it in social-style feeds at three scopes.
- **User Story:** As a user, I can add typed entries to a track and see them in infinite-scroll, filterable feeds at home, in a space, or in a track—like a social feed or group page.
  - **Requirements:**
    - **Entry Types:** Each track’s **track-attached** **`OperationalModel`** **always** includes at least one **default** **EntryType**; users add types under that profile; schema via YAML → JSON. Optional **library** **merge** adds **more** types into the attached Operational Model.
    - **Rich Entry Creation:** Title, body, attachments, dates, assignees.
    - **No entry-level visibility:** Access to read/edit entries follows **track + space collaborator model** and track **`visibility`** only.
    - **Tagging:** Tags on entries and (where product exposes) other entities—**everything can be tagged**.
    - **Feeds:** Default **view** for entry posts is **feed**; reverse-chronological; **cursor-based** infinite scroll; filters (e.g. tags, type, date, track).
    - **Temporal Organisation:** Timestamps on entries; date-range filters.

### Epic 3: Adaptive Views

- **Goal:** Provide multiple lenses to interact with the same Track data.
- **User Story:** As a project manager, I can switch my “Q4 Product Launch” Track from the Feed view to a Kanban board view based on status, so I can manage this specific project’s workflow visually.
  - **Requirements:**
    - **View Layer:** Presentation layer queries underlying track entries.
    - **Kanban, Table, Calendar, Gallery** as today; drag‑drop and column mapping where applicable.
    - **View Intelligence:** Multiple saved views; sync across views; per-view filters and sort.
    - **View Persistence:** Named views per track; optional default view.

### Epic 4: Google Docs–Style Collaboration & Organizations

- **Goal:** Predictable sharing on spaces and tracks; org member pool with creation rights; ownership transfer.
- **User Story:** As an owner, I share a space or track with collaborators using **viewer** / **editor**; I can invite existing Integral users or external users by **email**; I can **transfer ownership** by making another user **owner**. As an **org admin**, I add members and control who may create **spaces** and **tracks** under the org.
  - **Requirements:**
    - **Roles on Space/Track/Entry (sharing):** **`owner`** (via **`OWNS`**), **`editor`**, **`commenter`** (read + post comments only), **`viewer`** (via **`COLLABORATES_ON`** for collaborators). Shared users appear as **collaborators**.
    - **Access model — inheritance with explicit deny:** Space collaborators inherit access to **every** track in that space; track collaborators flow through to every entry in that track. Owners at any level may **add** direct collaborators (stacked access) or **explicitly exclude** an inherited user from a specific space/track/entry (via `EXCLUDED_FROM`). Direct collaborators are never blocked by exclusions — exclusion targets inherited paths only.
    - **Entry-level collaborators** (Phase 5) stack on top of the track cascade. They grant access to a single entry without granting access to the surrounding track; they never replace track/space rules with arbitrary per-entry ACLs.
    - **Organization-workspace membership is NOT a cascade source.** Org-workspace members must be explicitly added to spaces/tracks (per-user via `COLLABORATES_ON`, or workspace-wide by the owner setting `visibility="organization"` — itself an explicit opt-in for the whole non-guest member pool). Cross-workspace sharing auto-grants a `guest` `IS_MEMBER_OF` edge so the recipient can reach the shared resource.
    - **Share-links and resource-level invitations:** Owners may mint tokenized **share-links** (`POST /{spaces|tracks|entries}/{id}/shares`), redeem with `POST /shares/redeem`, revoke with `DELETE /shares/{id}`. Resource-level invitations (`POST /{spaces|tracks|entries}/{id}/invitations`) sit alongside workspace invitations and surface in `GET /me/invitations`; shared resources appear in `GET /me/shared`.
    - **Ownership transfer:** Current owner promotes a collaborator to **owner** (implementation: **`OWNS`** / edge migration—**ARCHITECTURE.md** §9).
    - **Organization:** Admin-managed **member pool**; selective rights (e.g. `canCreateSpaces`, `canCreateTracks`) for members; org-hosted spaces and standalone tracks; members with rights may create spaces/tracks for collaboration.
    - **Optional saved-view access rules** only if product enables; **not** entry-level ACLs.
    - **Comments & @Mentions**, activity history, export where applicable.
    - **Real‑Time Indicators** where supported.

### Epic 5: AI Conformability & Intelligence

- **Goal:** Reduce setup friction to zero and enhance organisation intelligently. The **resident harness** is **Operational Model-aware** — it understands and operates on Operational Models, not just raw CRUD. (Harness contract: [RESIDENT_HARNESS.md](RESIDENT_HARNESS.md). Canonical profile-authoring tool names — `integral_propose_model_revision`, `integral_publish_model_draft`, etc. — are specified in [AGENT_CONTRACT.md](../operational-models/AGENT_CONTRACT.md); the `integral_author_model` / `integral_modify_model` / `integral_list_models` names below are the original PRD intent, superseded by that contract.)
- **User Story 5.1:** As a new user, I can describe my goal (“Plan a website redesign”), and the AI will suggest a **Operational Model** from the library and apply it to a new Track — or **author a new Operational Model** if no suitable library match exists.
  - **Requirements:** Recommend **operational models**, templates, tags, and an initial view. If no match, the agent authors a minimal profile from the description using `integral_author_model`.
- **User Story 5.2:** As I use a track, the AI suggests modifications to the attached Operational Model — adding fields that would be useful, tags that would organize entries better, or views that would surface insights.
  - **Requirements:** Context-aware profile modification suggestions surfaced during use. Agent uses `integral_modify_model` to apply changes after user confirmation.
- **User Story 5.3:** Tag/type suggestions, relationship hints, view tips — bounded by opt-in and privacy.

### Epic 6: Operational Model Ecosystem

- **Goal:** Operational Models are **living, composable specifications** — not static templates. Every Space and Track has an attached Operational Model that can be applied from a library, customized in-place, authored by AI agents, and shared back to the library.
- **User Story 6.1:** As a user, my space and each track **always** have an attached Operational Model I can **customize**; I **may** pick a **library** package to **extend** that default; after merge, I can add, remove, reorder, or modify any element in-place.
  - **Requirements:**
    - **`HAS_OPERATIONAL_MODEL`** on **Space** and **Track**; optional **`DEFINES_TRACK_PROFILE`** from space-attached Operational Model.
    - **Standard library manifest** (canonical v1: **`operational_model_schema_version`**, **`scope: track`** or **`scope: space`**) (**ARCHITECTURE.md** §5.3).
    - **In-place customization:** After merge, the user can modify any EntryType, Tag, or View in the attached Operational Model. Changes update the manifest (living specification) and are reflected in materialized nodes.
    - **Provenance tracking:** The attached Operational Model's `manifest` tracks which elements came from which library version, enabling intelligent re-merge of upstream updates with local customizations.
    - **Profile-aware Track/Space creation:** Creating a Track or Space optionally applies a library Operational Model. If no profile is specified, the Default profile is created.
- **User Story 6.2:** As a user, I can **derive a new library Operational Model** from an existing customized Track or Space, and publish it back to the library.
  - **Requirements:**
    - `POST /operational-models/from-track/{trackId}` and `POST /operational-models/from-space/{spaceId}` endpoints.
    - Attribution: the derived profile records the original as provenance.
- **User Story 6.3:** As an AI agent, I can **author** new Operational Models from a natural language description, **modify** attached Operational Models, and **recommend** profiles from the library.
  - **Requirements:**
    - MCP tool `integral_author_model`: accepts a natural language description, generates a valid manifest, validates it, and publishes to library or applies directly.
    - MCP tool `integral_modify_model`: modifies an attached Operational Model (add/remove/modify EntryType, Tag, View). Updates both manifest and materialized nodes.
    - MCP tool `integral_list_models`: lists available library Operational Models, filtered by scope or keyword.
    - `integral_create_track` and `integral_create_space` must resolve `type_hint` to a library Operational Model.
  - **Library sources:** Platform, organization-private, community (per policy); **read** for all authenticated users; **publish** restricted to admins/publishers (agents use `integral_author_model` which validates and publishes on behalf of the user).
  - **Merge extends Default:** **Library** **merge** updates **attached** profile subgraphs only; **library** nodes unchanged; runtime uses **track-attached** **`OperationalModel`** for **EntryType** / **Tag** / **View** resolution.

---

## 7.0 Technical Specifications & Constraints

### 7.1 Core Architecture: Built on jvspatial
Integral is architected on **jvspatial**, a spatial computing framework where all core entities are modelled as **Nodes** within a persistent graph. This choice enables:
- **Native Graph Operations:** **`App`** connects to registries including **`Users`**, **`Spaces`**, **`Organizations`**, **`Tracks`**, and **`OperationalModels`** (**library** **`CATALOGS`** **`OperationalModel`**); **Space** / **Track** **`HAS_OPERATIONAL_MODEL`** **attached** **`OperationalModel`**; **EntryType** / **Tag** / **Views** live under **track-attached** profiles; edges (**`OWNS`**, **`COLLABORATES_ON`**, **`CONTAINS`**, **`HAS_OPERATIONAL_MODEL`**, **`TAGGED_WITH`**, …) power permissions, feeds, and real-time updates.
- **Spatial Computation for Views:** Filtering, sorting, and transforming entry collections into different views is executed as parallel dataflow kernels.
- **Unified AI/ML Integration:** AI services operate directly on the graph.

### 7.2 Data Model
- **Flexible Schema:** Entry types and custom fields as JSON on Entry / EntryType nodes; Markdown bodies.
- **Flat track hierarchy:** No nested sub-tracks; use **Spaces** to group tracks.
- **No per-entry visibility field** in product requirements; backend may deprecate/remove `visibilityRule` on entries (**ARCHITECTURE.md** §13).
- **Real‑Time Sync:** WebSockets or SSE with deltas; MVP may poll.

### 7.3 AI/ML Integration
- **Microservice Architecture:** Tag suggestion, profile recommendation, etc., as separate services.
- **Privacy & Control:** AI optional; user can disable.

### 7.4 API & Extensibility
- **Headless API:** REST under `/api` (see **ARCHITECTURE.md** §6.2), including feed, spaces, tracks, **workspaces**, members, tags, **operational models**, **access** (collaborators / exclusions), **shares** (share-links), **invitations**, **`/me/shared`**, **`/me/invitations`**, and meta.
- **YAML / package definitions:** Entry types, views, and **operational model** manifests.
- **Workspace scope header:** Backend-authoritative `X-Integral-Scope: <workspace_id>` is required on list endpoints (W5); cross-workspace reads are refused.

### 7.5 Backend Refactor Notes
Aligning with this PRD may require **API and persistence changes**: remove entry-level visibility enforcement; add **ownership transfer** transactions; extend **`IS_MEMBER_OF`** (or equivalent) with **creation capabilities** and **guest** role for cross-workspace shares; extend **tagging** to multiple entity types; implement **`HAS_OPERATIONAL_MODEL`** on **Space** / **Track**, **`App` → `OperationalModels`** **library**, **merge** transactions into **attached** profiles, and **GET**/**PATCH** for attached Operational Models (**never** live-bind runtime resolution to **library** nodes after merge). **Backend-authoritative workspace scope** via the `X-Integral-Scope` header (W5). See **ARCHITECTURE.md** §13.

---

## 8.0 Success Metrics (KPIs)

| Metric | Target (MVP Launch) | Measurement Method |
| :----- | :------------------- | :------------------ |
| **Activation Rate** (User creates first Track & 3 entries) | > 40% of sign‑ups | Analytics tracking |
| **Flexibility Adoption** (User who uses >1 view type) | > 30% of weekly active users (WAU) | Analytics tracking |
| **Collaboration Depth** (Track or Space with >2 members) | > 20% of Tracks or Spaces created | Backend audit |
| **AI Suggestion Acceptance Rate** | > 25% of prompts shown | AI service logging |
| **Personal Use Case Adoption** (% using Event/Personal profiles) | > 15% of Tracks created | Backend audit |
| **Operational Model Reuse** | > 50% of new Spaces/Tracks use a profile | Backend audit |
| **Profile Customization** (% of applied profiles customized in-place) | > 30% of applied profiles | Backend audit |
| **Agent Profile Authoring** (agent-authored or agent-modified profiles) | > 10% of profile changes | Backend audit |
| **Library Growth** (profiles in library beyond seeded) | > 5 community/published profiles | Backend audit |
| **NPS (Net Promoter Score)** | > 40 | User survey |

---

## 9.0 Out‑of‑Scope for MVP

- Public sharing of Tracks via **unauthenticated** links.
- Mobile‑native applications (responsive web is mandatory).
- Advanced AI features like automated relationship mapping or predictive timelines (beyond basic suggestions).
- Third‑party integrations (Zapier, Slack, etc.)—later phases.
- Custom view plugin ecosystem (developer SDK).
- Formal hierarchies or sub‑tracks (use **Spaces**).
- **Per-entry visibility / ACLs** — intentionally not supported. Access follows track / space / workspace rules. Entry-level *collaborators* (Phase 5) stack on top of that model but never replace it with arbitrary per-entry ACLs.
- On‑premise deployment (enterprise feature for Phase 3).

---

## 10.0 Open Questions & Risks

| Risk / Question | Mitigation / Action |
| :--------------- | :------------------- |
| **Risk:** Overwhelming users with too many options early on. | **Mitigation:** Progressive disclosure; default to Feed; advanced views one click away. |
| **Risk:** Performance ceiling for real‑time updates in complex Kanban with 10+ collaborators. | **Action Item:** Spike with jvspatial kernels (large entry sets, concurrent users). |
| **Risk:** Ownership transfer and org capability edge cases (billing, audit). | **Action Item:** Define transfer and admin policy in **ARCHITECTURE.md** §9 / §13. |
| **Risk:** AI suggestions feel irrelevant. | **Mitigation:** Conservative, rule-based start; tooltips; feedback loops. |
| **Risk:** Users overstuff Tracks. | **Mitigation:** UI copy, onboarding, AI nudge to split tracks. |

---

## Appendix A: Glossary

- **Space:** Groups related **Tracks**; may be personal or under an **Organization**. Collaborators invited to a space get access to contained tracks by default unless refined per track.
- **Track:** A container for a single, focused project or area; entries live here; **visibility** is at track level.
- **Collaborator:** User with **`viewer`** or **`editor`** on a Space or Track via sharing (**`COLLABORATES_ON`**).
- **Owner:** User with **`OWNS`** on a Space or Track; can share, transfer ownership, and delete per policy.
- **Organization:** Product-surface term for an org-kind **Workspace**. Backed by `Workspace(kind="organization")` with an admin-owned **member pool** (`IS_MEMBER_OF` role `admin | member | guest`); selective rights to create org **spaces** / **tracks**; can host grouped or standalone tracks. The legacy standalone `Organization` node was retired in favor of the unified `Workspace` model.
- **Workspace:** Top-level container — `Workspace(kind="personal")` (auto-created on signup) or `Workspace(kind="organization")` (org member pool). Backend-authoritative scope via the `X-Integral-Scope` header gates every list endpoint.
- **Share-link:** Tokenized, redeemable URL minted on a Space / Track / Entry; redemption auto-grants a `guest` workspace membership when crossing workspaces.
- **Commenter:** Collaborator role between viewer and editor — read + post comments, no entry edits.
- **OperationalModels:** Registry under **`App`** listing **library** **`OperationalModel`** packages (browseable by all authenticated users).
- **Operational Model (attached):** **Default** **`OperationalModel`** per **Space** and per **Track** (**`HAS_OPERATIONAL_MODEL`**). **Track-attached** profile **owns** **entry types**, **tags**, and **views** for that track. **Space-attached** profile **may** **`DEFINES_TRACK_PROFILE`** **track-template** profiles. **Library** **merge** **extends** these **Defaults**. **Provenance:** **`attachedOperationalModelId`**, **`libraryMergeSourceId`**. Runtime resolves types/tags/views via **track-attached** profile, not **library** nodes after merge.
- **Entry:** Fundamental unit of content in a Track; access follows **track** (and space) permissions only.
- **Entry Type:** Blueprint for entries (custom fields / YAML schema).
- **Tag:** Label attachable across entities (org, space, track, entry, view, profile) per architecture.
- **View:** Saved presentation of entries (Feed, Kanban, Table; **Calendar** and **Gallery** are specified in architecture but **deferred in the UI** until dedicated renderers ship).
- **Node / Edge:** Base graph constructs in jvspatial.

---

## Appendix B: User Flow Example (Eldon, the Founder)

1. Eldon signs up and is prompted to describe his first Track: “Manage the beta launch of my AI division.”
2. AI recommends a **Product Launch** operational model: Entry Types (Task, Meeting Note, Risk, Milestone); tags; default Kanban view.
3. Eldon accepts and names the Track “AI Division: Beta Launch.”
4. He adds entries: tasks, notes, milestones.
5. He creates a **Space** per venture, moves related tracks inside, and **shares** the space with his co‑founder and engineers as **editors** (Google Docs–style).
6. For investor-only material, he uses a **separate track** with **viewer**/**editor** invites limited to trusted collaborators (no per-entry hiding).
7. He switches to Kanban to see progress; his **home feed** aggregates entries across spaces and tracks he can access.
8. Later, he **transfers ownership** of one track to his co‑founder when ownership of that initiative changes.

---

## Appendix C: MVP collaboration — email invites

The MVP ships **user-picker collaboration** only: collaborators must already have a Integral account. **Email-based invitations** to people without an account (tokenized signup / provider integration) are **deferred** to a later release. Product and UI copy should not promise email invites until that work ships.

---
