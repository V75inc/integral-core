# Integral architecture

Integral is a domain-neutral operational substrate with a web experience, an always-on agentive layer, and published App extension contracts. This reference describes the current staging implementation. The [white paper](WHITE_PAPER.md) explains the design; [invariants](../INVARIANTS.md) define constraints changes must preserve.

## Runtime components

| Component | Responsibility | Source |
|---|---|---|
| React/TypeScript frontend | Views, records, sharing, conversation, review | `frontend/src/` |
| jvspatial/FastAPI backend | Routes, graph entities, lifecycle, auth integration | `backend/app/main.py`, `api/`, `models/` |
| Services | Canonical writes, permissions, schema and App lifecycle | `backend/app/services/` |
| Agentive layer | Harness bindings, MCP, skills, staging, work | `backend/app/agentive/` |
| Public App facade | Scoped get/query/invoke and declared capability access | `backend/app/contracts/`, `sdk/python/` |
| Persistence adapters | Graph and Object records, transaction/CAS guarantees | jvspatial adapter selected at boot |

The source UI uses Vite at port 9006 and the backend at 4000. Containers and hosts can map different ports. A wheel includes the UI and serves it with `integral web`.

## Rooted knowledge graph

Graph participants are jvspatial Nodes connected through explicit Edges. Rooted registries organize collections. The user hierarchy is Workspace → App → Track → Entry; actual structural paths include registries such as Apps, Tracks, ChatThreads, and OperationalModels.

Every persisted Node must attach to a valid rooted parent in the same unit of work. A subsystem participant extends from its App-bound anchor rather than floating beside it. Scalar IDs are lookup caches, never a replacement for structural edges. Non-graph, log-shaped records use Object persistence where appropriate.

The main entities are Workspace, App, Track, Entry, OperationalModel, EntryType, Tag, View, Dashboard, ApplicationDefinition, ChatThread, HarnessSession, Skill, Invitation, ShareLink, Comment, Attachment, Policy, Conflict, and Approval. Agentive participants add their own rooted subsystem entities. See `models/nodes.py`, `models/edges.py`, and the agentive models for authoritative fields.

Typed edge metadata carries relationship state. `COLLABORATES_ON` carries a role; `REFERENCES` carries the relation field identity; membership and lifecycle edges express their own semantics. Multi-hop computation normally uses a Walker. Measured bulk-query deviations must preserve graph and schema integrity and document their evidence.

## Operational Models and application definitions

An attached OperationalModel supplies schema and composition. App-attached models may define track profiles through `DEFINES_TRACK_PROFILE`. Entry validation materializes declared relation and member edges. Authoring YAML version 3 compiles to runtime schema version 2.

An installed App also has an immutable active ApplicationDefinition, attached through `HAS_APPLICATION_DEFINITION`. Its manifest, requirement ledger, and provenance establish the effective application contract. Active scalar pointers must agree with that authority. A changed contract appends a revision and supersedes its predecessor.

Package files, library models, attached models, installed Apps, and active definitions are distinct artifacts. Do not authorize an operation solely from a library manifest or treat an attached schema as the entire installed App contract.

Draft publication checks validation and impacts. Supported migrations use one dispatcher and durable tracking. Entry writes are rejected while their applicable schema is queued or in progress. Forced publication is separately authorized and does not automatically repair old records.

## Scope and access

Explicit request scope is `X-Integral-Scope: ws:<workspace_id>`. A malformed supplied header returns 400. Unknown or inaccessible workspaces are rejected without becoming an enumeration surface. Omitted ordinary request scope resolves to the personal default; specialized internal paths can explicitly use stored active scope. See `request_scope.py` and `workspace_resolver.py` for exact precedence at the relevant call site.

Resource roles are owner > admin > editor > commenter > viewer. Workspace membership roles are separate. Direct grants retain their role; inherited owner/admin authority is capped at editor. Exclusions remove inherited paths, while direct ownership and collaboration win over them.

Workspace membership gates private workspace content. Explicit public-read and sharing paths provide narrow exceptions rather than general write or enumeration authority. Current permission is re-evaluated during tools, approvals, and recovery. A cached result, session ID, or client-selected resource cannot widen authority.

There is no general field-level privacy. Put differently governed information behind separate resources and preserve restricted navigation behavior. Filters and rendering rules are not access controls.

## Application extension perimeter

Core does not import domain packages into service, API, schema, or model logic. Apps use `ToolContext` and published contracts. Preferred access uses scoped `get`, `query`, and `invoke`; legacy facade methods remain subject to their defined boundaries.

A package declares tools, hooks, operations, queries, skills, views, and assets. Python tools require the appropriate trust tier and production signature posture. Hook points are cataloged and frozen. Installation registers package-owned capabilities in scope; pause and removal withdraw the applicable registrations.

Declared queries carry contract mode and output shape. Operations carry input shape, policy action, and staging requirements. Extension iframe views use the hosted asset/handshake boundary; arbitrary remote scripts are not a replacement for that contract. Host extensions are explicitly configured outside Core.

## Experience and projections

The core view palette supplies registered views and widgets. Declarative composites resolve to supported primitives. Code plugins need reviewed registry and backend compatibility; they are not Python or JavaScript embedded in a data manifest.

Package-owned App Home resolves its data from the active application contract. Declared queries must return complete rows and totals as required. Failures show unavailable states rather than fabricated zeros. Home actions prepare editable chat drafts. Dashboards are separately mutable instance projections with their own query and permission checks.

Named relation, member, and file values resolve through authorized APIs. Raw IDs remain in machine payloads, not normal human-facing values. Audit and provenance presentation use shared normalization components.

## Intelligence perimeter

The agentive layer is always loaded. `AGENTIVE_ENABLED` is not a supported boot gate. The default resident is Integral AI through Pydantic AI; Integral AI is the only resident harness; There is no harness selector.

There is one resident per active binding, faceted by principal and scope. External agents use MCP. The retired A2A fabric is absent. Skills are instruction artifacts discovered progressively; active accessible Apps in the current workspace determine the overlay.

Authenticated identity, tenancy, provider route, and credential authority are server-derived. The harness performs reasoning and model interaction. Core owns capabilities, policy, staging, effects, transcripts, and immutable observations. See [resident harness](RESIDENT_HARNESS.md) and [BYOA](BYOA.md).

## Transcripts, sessions, and durable work

ChatThread and ChatMessage are product conversation records. HarnessSession is rooted beneath the thread and bound to principal, workspace, binding, and generation. Encrypted Object records hold detailed execution history and checkpoints in the same namespace. Provider handles are opaque continuation hints, not identity.

Native durable chat is off by default. When enabled, accepted request fingerprints, worker input, event replay, stable transcript writes, and lease-fenced execution must agree. Content verification includes message parts, metadata, parent, and typed host context; matching tenant and message ID alone is insufficient.

WorkItems use current lease token and fence for authority. Brokered effects use deterministic run/effect/logical-step identity. PostgreSQL commits work transitions with outbox and approval state atomically where specified. Routine and event adapters enqueue idempotently. Uncertain effects require reconciliation.

Production work fails closed without the required store indexes and public transaction/CAS contracts; Mongo is not a qualified production work store. JSON/SQLite are single-worker development stores. Shared-store contracts do not by themselves qualify all multi-worker user journeys.

Bounded WorkMandate review is not a runnable public admission route. Approved physical model calls require trusted bounds/pricing, shared holds, intent receipts, and last-moment readiness checks. Ordinary chat work does not acquire mandate authority merely by having a work context.

## Files, retrieval, and integration

Attachments have structural ownership, server-validated media type, content identity, authorized reads, and bounded extraction. The default scanner is `noop`; operators must configure and qualify a real scanner to claim malware screening.

Retrieval filters permissions and scope before exposing records. Semantic behavior depends on the configured backend and index; the presence of pgvector in a container does not establish that every retrieval path uses it. Native connectors and external MCP connectors retain explicit source and lifecycle boundaries.

## Vision-aligned architectural directions

The architecture aims to make application generation increasingly complete while keeping Core generic. Remaining work includes public bounded-work admission, provider and connector matrices, comprehensive interruption/recovery qualification, and wider shared-worker acceptance.

A feature's implementation, package publication, CI, local browser acceptance, and production qualification are separate facts. The [qualification guide](../ops/QUALIFICATION.md) records that distinction. The [roadmap](ROADMAP.md) describes priorities without attaching invented release dates.
