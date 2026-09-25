# ADR 010 — Connector subsystem architecture (modular MCP + native)

**Status:** Accepted
**Date:** 2026-08
**Renumbered:** 2026-09 from ADR-006 so the number does not collide with
[ADR-006 unstaged Personal Context writes](006-personal-context-stream-unstaged.md).
MCP client mount: [ADR-009](009-mcp-as-connector.md).

**Amendment (2026-08):** The package catalog shipped as in-repo YAML under
`backend/app/connectors/catalog/*.yaml`, not as a Operational Model sidecar.
Presence in that directory is the vetting gate. Settings **Connector library**
consumes `GET/POST /agentive/connectors/catalog…`. The Official MCP Registry
is **not** the product browse path (client code may remain unused). Authoring
guide: [connectors.md](../connectors.md).

**Amendment (2026-09):** Added connection scoping (`connection_mode`: `"per_user"` vs
`"shared"`), operator `label`, dynamic caller resolution precedence
(`resolve_connector_row`), canonical tool refcounting (`_CANONICAL_MCP_ROWS`), and
custom MCP server mounting (`POST /api/agentive/connectors/mcp/mount`). Personal
workspaces strictly enforce `per_user`; collaborative workspaces require admin role
for `shared` install. Members in collaborative workspaces receive read/invoke
permissions on shared rows (`tool.invoke`, `connector.read`). Details:
[connectors.md](../connectors.md) §5 and [ADR-009](009-mcp-as-connector.md).

## Context

ROADMAP Theme B calls for a first-class connector subsystem: modular packages
Integral can house, external knowledge mirrored into the graph, and a path toward
hero integrations (Jira, Gmail, Drive, Slack, HRIS/CRM/Calendar) plus
marketplace UX (browse, authorize, configure, monitor).

Partial implementation already exists:

- **Native adapters** — `SyncConnector` ABC, sync runtime/scheduler
  (`backend/app/services/connectors/`), reference connectors Gmail, QuickBooks,
  GitHub Issues (`backend/app/agentive/connectors/`).
- **MCP client mount** — live external MCP servers as workspace connectors
  ([ADR-009](009-mcp-as-connector.md)).
- **Graph model** — `Connector` Node, `IS_CONNECTED_TO` track bindings
  (I-CON-02), provenance split shape (I-CON-01), per-connector Policy at
  create (I-CON-04).
- **Inbound hooks** — `connector.dedup` and `connector.auto_link` fire after
  sync materialization (`backend/app/services/hooks/connector_runtime.py`).
- **Operational Model packages** — library CPs for connector entity shapes
  (e.g. `backend/app/packages/github-issues/operational-model.yaml` per I-CON-05).

What was missing is a **unified architecture ADR** that names how native sync
adapters and MCP mounts share one subsystem, how modular packages are
discovered and installed, how credentials align with existing BYOK patterns, and
where outbound write-back is explicitly deferred.

This record is the Theme B architecture authority. [ADR-009](009-mcp-as-connector.md)
remains the locked implementation spec for the MCP-client path.

## Decision

### 1. Two connector kinds, one Node model

All connector instances are `Connector` Nodes with a canonical `User —OWNS→
Connector` edge. Kinds differ by runtime behaviour, not by node type.

| Kind | Role | Runtime | Sync pull? | Tool surface? |
|------|------|---------|------------|---------------|
| **Native adapter** | Mirror external entities → Entries | `@register_sync_connector(slug)` subclass | Yes (`sync_pull`) | No — graph is the query API |
| **MCP mount** | Proxy live remote tools to resident/agents | ADR-009 `mcp_mount` / `mcp_proxy` | No | Yes — `mcp__{short}__{tool}` workspace keys |

**Decision tree (authoring):**

```
Need durable mirrored Entries in the graph?     → Native adapter (SyncConnector).
Need live RPC to an external MCP tool surface?  → MCP mount (ADR-009).
Need both (e.g. Jira sync + Jira MCP tools)?    → Hybrid package (reserved; not v1).
Bridge/proxy external API without mirroring?    → Rejected — mirror model stands (ROADMAP).
```

Distinguish instances via `Connector.subclass_slug`:

- Sync connectors: slug matches `@register_sync_connector` key (e.g.
  `github_issues`, `gmail`, `quickbooks`).
- MCP mounts: `subclass_slug = "mcp"` (ADR-009).

Hybrid (`kind: hybrid`) is reserved for a future package that ships both a
`SyncConnector` and an MCP transport config under one catalog entry. v1 treats
them as separate instances or separate packages.

### 2. Modular connector package manifest

**v1 catalog (shipped).** Each vetted package is one YAML file:

`backend/app/connectors/catalog/<slug>.yaml`

`slug` MUST equal the filename stem. The loader
(`backend/app/connectors/catalog_loader.py`) fails loud on invalid YAML or
missing keys. Being present in this directory **is** the vetting gate — the
library UI does not list anything else.

Native sync code still lives under `backend/app/agentive/connectors/`
(I-CON-05). Seeded Operational Models for mirrored EntryTypes still live under
`backend/app/packages/<slug>/operational-model.yaml`. Those are **not** the browse
catalog; they are optional companions for native adapters.

```yaml
# backend/app/connectors/catalog/<slug>.yaml
slug: github_issues          # must match filename
display_name: GitHub Issues
description: Mirror issues from a GitHub repository into an Integral track.
category: native             # native | mcp_server | mcp_package
kind: sync                   # sync | mcp  (hybrid reserved, not v1)
icon: github                 # frontend ConnectorBrandIcon key
auth:
  type: env                  # oauth2 | api_key | env | none | headers
  fields:
    - name: owner
      label: Repository owner
      secret: false
      required: true
implementation:
  sync_connector_class: github_issues   # @register_sync_connector key (sync only)
# MCP-only keys:
# transport: streamable_http | stdio
# url: https://mcp.example.com/mcp
# command: npx
# args: ["-y", "some-mcp-server"]
```

**Field semantics:**

- `kind` — selects runtime dispatch (`sync` → `_catalog_install_sync` /
  `sync_runtime`; `mcp` → `_catalog_install_mcp` / ADR-009 mount).
- `category` — library filter chip only (`native` / `mcp_server` /
  `mcp_package`). Does not change runtime.
- `auth.type` + `auth.fields` — install-sheet keys posted as
  `CatalogInstallRequest.secrets`. `secret: true` renders a password field.
- `implementation.sync_connector_class` — native only; MUST match
  `@register_sync_connector("<slug>")` (I-CON-03).
- `transport` / `url` / `command` / `args` — MCP only. HTTP mounts use
  `mcp_adapter.mount_mcp_connector`; stdio uses `mcp_mount.mount_mcp_connector`.
- `icon` — frontend map in `ConnectorBrandIcon`; unknown keys fall back to
  the generic MCP mark.

A future `connector.yaml` sidecar beside a Operational Model remains allowed as
**optional CP metadata**; it is not the catalog source of truth.

Packages are **modular**: Integral ships first-party packages in-repo;
third-party packages follow the same YAML shape (future: signed distribution
— out of scope).

### 3. Three-layer registry model

“Registry” in Theme B means **internal package and instance bookkeeping**, not
an external MCP server directory (Smithery-style indexes may enrich the catalog
later; they are not a runtime dependency).

| Layer | Purpose | Source of truth |
|-------|---------|-----------------|
| **Package catalog** | What connector types exist; browse/install metadata | `backend/app/connectors/catalog/*.yaml` → `GET /agentive/connectors/catalog`, `POST …/catalog/{slug}/install` |
| **Class registry** | Slug → `SyncConnector` implementation | `register_sync_connector` in `app/services/connectors/registry.py` (I-CON-03); populated at agentive import |
| **Workspace instances** | Per-workspace/per-user live connectors | `Connector` Nodes + `IS_CONNECTED_TO` edges + `auth_state` |

**Catalog vs marketplace UX:** Settings **Connectors → library** is the v1
browse/install surface. ROADMAP’s broader marketplace (monitor, ratings,
third-party distribution) remains a later product phase.

**Official MCP Registry:** Not on the product path. In-repo YAML is the only
vetted list. Registry client/UI may exist unused; do not route operators
through an unvetted remote index.

**MCP-specific note:** MCP mounts do not register in the sync class registry.
Tool discovery uses live `tools/list` at mount/refresh time (ADR-009). The
package catalog may include a generic `mcp` package entry describing transport
config schema (`stdio` / `streamable_http`) without listing remote tool names
(tools are discovered at runtime).

### 4. Per-workspace config and credentials

**Instance config:**

- `Connector.workspace_id` — denormalized cache for workspace-scoped tool
  registration and list filters (ADR-009; I-GRAPH-01 ownership remains
  `OWNS`).
- Track bindings — `Connector —IS_CONNECTED_TO→ Track` edge metadata carries
  per-binding mapping YAML (I-CON-02). One connector MAY bind multiple tracks
  (e.g. issues → Track A, PRs → Track B).
- `sync_interval_seconds`, `sync_cursor`, `last_synced_at` — sync scheduler
  consumes these (native adapters only).

**Credentials — align with [ADR-001](001-model-credentials-byok.md) patterns:**

| ADR-001 pattern | Connector application |
|-----------------|----------------------|
| Encrypt at rest | OAuth tokens / API keys in `Connector.auth_state` encrypted with `INTEGRAL_CREDENTIAL_ENC_KEY` when persistence lands (today: redact-on-wire; encryption migration deferred) |
| Plaintext never in graph audit snapshots | GitHub Issues reads token from env each pull — never persists token to `auth_state` (reference pattern) |
| Validate on save | OAuth callback + mount endpoints validate credentials before marking connector healthy |
| Redact on wire | `mcp_safe_auth_state`, Gmail/QB response helpers strip `env`, `headers`, tokens from API responses |
| Workspace owner posture | Org workspaces: connector auth SHOULD resolve to workspace admin/owner for shared integrations (same billing/trust posture as ADR-001 `billing_user_id`) |

**Storage evolution:** When token payloads outgrow inline `auth_state`, introduce
`ConnectorCredential` as jvspatial `Object` (I-GRAPH-02) keyed by
`(connector_id, credential_slot)` — same rationale as `UserModelCredential`.
Implementation deferred; this ADR locks the pattern only.

### 5. Inbound sync rails (existing — do not rebuild)

Inbound flow for **native adapters**:

```
sync_pull → materialize Entry → provenance (I-CON-01)
           → connector_runtime.fire_dedup_hooks
           → connector_runtime.fire_auto_link_hooks
```

- **`connector.dedup`** — declarative match against existing Entries; actions
  include skip, update, or `link_references` (see CRM `qb_customer_to_contact`).
- **`connector.auto_link`** — tool or declarative binding to create `REFERENCES`
  edges after materialization (see CRM `gmail_thread_to_contact` →
  `normalize_email_for_match`).

Hook bindings register at **App bundle install** (`hooks[]` in profile
manifest) or future connector package install. Sync runtime already invokes
`connector_runtime` after each create/update — packages declare hooks; they do
not implement custom sync loops.

**MCP mounts** do not participate in inbound sync rails — they have no
`sync_pull`. Remote tool results are ephemeral RPC responses unless a tool
explicitly writes Entries through normal substrate APIs (which then follow
standard provenance, not connector sync provenance).

### 6. Outbound gap (explicit follow-on)

v1 connector architecture is **pull-only / read-only mirror** for native
adapters and **live tool invoke** for MCP mounts. The following are **not** in
v1 scope:

- Bidirectional write-back (Jira comment, Gmail send, QB invoice create).
- `capabilities.push: true` in package manifests.
- New PolicyActions such as `connector.write` (design reserved).

**Outbound follow-on requirements (named now, implemented later):**

1. **Draft-only / staged delivery gates** — nothing leaves the workspace
   without human bless (produce resident contract: “Outbound delivery is staged
   or draft-only”).
2. **Explicit PolicyActions** — per-domain write grants on connector subject +
   caller dual-gate (mirror MCP invoke pattern).
3. **Audit** — every outbound action emits ChangeEvent with actor, connector,
   external target, staging state.
4. **Rate limits** — per-connector quotas (ARCHITECTURE §22.3).

**MCP invoke vs outbound write-back:** Resident calling `tools/call` on an MCP
connector is **tool invocation** (ADR-009 `tool.invoke`), not graph
write-back. Whether a specific MCP tool constitutes an “outbound delivery”
(e.g. `send_email`) is a **policy + staging classification** problem for the
follow-on — package manifest MAY tag tools with `side_effects: write_external`
when outbound phase lands.

### 7. Permission gating (policy engine)

All connector authorization flows through `policy_engine.evaluate()`
(`backend/app/services/policy_engine.py`). Connectors fail closed without an
attached Policy (I-CON-04).

**Policy materialization at create** (`materialize_policies_for_connector`):

| Connector kind | Connector-subject PolicyActions |
|----------------|--------------------------------|
| Native adapter | `connector.sync`, `entry.create`, `entry.update` |
| MCP mount | `tool.invoke`, `connector.read` |

**Evaluation matrix:**

| Path | Subject evaluated | Action | Resource | Notes |
|------|-------------------|--------|----------|-------|
| Sync tick | `Subject(kind="connector", id=<connector_id>)` | `connector.sync` | Track scope at sync time | Sync runtime gate |
| Entry materialize | connector subject | `entry.create` / `entry.update` | Target track/entry | After sync gate passes |
| MCP tool invoke | **Dual gate:** (1) caller (human/agent) (2) connector subject | `tool.invoke` | `Resource(kind="connector", scope="connector:<id>")` | ADR-009; agents need explicit Policy |
| MCP health/read | connector subject | `connector.read` | connector scope | Health check endpoints |
| Human owner | default-human branch | — | `connector:<id>` scope | Owners bypass agent Policy requirement |

Callers (resident, BYOA MCP clients) never bypass connector-subject evaluation
on invoke. Admin grant-chain UX (ROADMAP M7) will eventually surface connector
Policies alongside human/agent grants.

### 8. Worked examples (manifest → code paths)

#### Example A — Native sync: GitHub Issues

**Package catalog entry** (`catalog/github_issues.yaml` + existing CP):

| Manifest field | Maps to |
|----------------|---------|
| `slug: github_issues` | `@register_sync_connector("github_issues")` in `backend/app/agentive/connectors/github_issues.py` |
| `operational_model.library_slug: github-issues` | `backend/app/packages/github-issues/operational-model.yaml` — defines `github_issue` EntryType, tags, views |
| `auth.type: env` | Token read from `GITHUB_TOKEN` env at pull time; not persisted |
| `capabilities.pull: true` | `GitHubIssuesConnector.sync_pull` → sync_runtime |

**Install/runtime path:**

1. Admin installs package → creates `Connector` with `subclass_slug="github_issues"`.
2. `materialize_policies_for_connector` → sync baseline Policy.
3. Admin binds Track via `IS_CONNECTED_TO` (mapping YAML on edge).
4. User merges library CP `github-issues` onto Track (or install flow merges automatically).
5. Sync scheduler ticks → `get_sync_connector("github_issues")` → pull →
   materialize Entries with provenance `source="connector"`,
   `source_id="<connector_id>:<issue_id>"`.
6. If App bundle hooks installed (e.g. CRM cross-links), dedup/auto_link fire
   post-materialize.

#### Example B — MCP mount: hosted HTTP server

**Package catalog entry** (`catalog/notion.yaml` pattern):

| Manifest field | Maps to |
|----------------|---------|
| `kind: mcp` | ADR-009; instance `subclass_slug="mcp"` |
| `transport: streamable_http` + `url` | `mcp_adapter.mount_mcp_connector` |
| `auth.type: none` (or `headers`) | No install fields; 401 starts MCP OAuth. Header fields become mount headers |
| `policy_baseline` | `tool.invoke`, `connector.read` via `materialize_mcp_policies` |
| No `sync_connector_class` | Not in `_SYNC_REGISTRY` |

**Install/runtime path:**

1. `POST /agentive/connectors/catalog/{slug}/install` → mount, discover tools,
   `register_workspace_tools(workspace_id, f"mcp:{id}", …)`. Raw
   `POST /agentive/connectors/mcp/mount` remains for non-catalog mounts.
2. Resident `EmbeddedIntegralAction.get_tools` overlays `mcp__*` keys when
   workspace scope set.
3. Invoke → `tool_dispatch` → `mcp_proxy.invoke_from_spec` → dual policy gate →
   MCP `tools/call`.
4. `POST …/mcp/refresh` re-lists tools without restart; boot
   `rehydrate_mcp_connectors()` restores sessions.

#### Example C — Hook bindings on App bundle (CRM)

CRM App manifest (`backend/app/packages/crm/operational-model.yaml`) declares inbound
hooks keyed by `connector_slug`:

```yaml
- point: connector.dedup
  key: qb_customer_to_contact
  match:
    connector_slug: quickbooks
    source_entry_type: qb_customer
  declarative:
    target_track_type: contacts
    match_field_pairs: [...]
    action_on_match: link_references

- point: connector.auto_link
  key: gmail_thread_to_contact
  match:
    connector_slug: gmail
    source_entry_type: email_thread
  tool: normalize_email_for_match
```

These bindings install with the CRM App bundle; sync runtime fires them when
QuickBooks or Gmail connectors materialize matching Entries — no connector-
specific code in sync_runtime.

## Consequences

- **ADR-009 unchanged** — MCP client mount remains the locked implementation;
  this ADR is the parent architecture.
- **No new invariants required** — existing I-CON-01…05 + ADR-009 fields
  suffice; catalog YAML is additive metadata, not a graph primitive.
- **Implementation sequencing:**
  1. ~~Catalog loader + list/get/install API~~ **landed** (`catalog_loader.py`,
     `GET/POST /agentive/connectors/catalog…`, Settings library).
  2. Automatic Operational Model merge on native install (still optional /
     operator-driven).
  3. First additional hero packages (Jira, Drive, Slack, …) as catalog YAML +
     native or MCP implementation.
  4. Broader marketplace UX (monitor, third-party distribution).
  5. Outbound / write-back phase with draft-only gates.
- **Third-party MCP indexes** — optional catalog enrichment; never required at
  runtime for mount/invoke.

## Alternatives considered

- **External MCP directory as core registry** — rejected; catalog describes
  Integral-housed packages; remote discovery is install-time convenience only.
- **Separate Node types for MCP vs sync** — rejected; one `Connector` model
  with `subclass_slug` dispatch keeps policy and CRUD unified.
- **Outbound in v1** — rejected; pull-only mirror + staged delivery discipline
  must land before write-back (ROADMAP + resident contract).
- **Bundle tools for MCP proxy** — rejected (ADR-009); violates bundle facade /
  ADR-003.

## Cross-references

- Theme B: [ROADMAP.md](../../product/ROADMAP.md) § Theme B
- Mirror model overview: [ARCHITECTURE.md](../../product/ARCHITECTURE.md) §22.3
- Invariants: [INVARIANTS.md](../../INVARIANTS.md) Phase 5 (I-CON-01…05)
- MCP client implementation: [ADR-009](009-mcp-as-connector.md)
- Authoring / adding packages: [connectors.md](../connectors.md)
- Credential patterns: [ADR-001](001-model-credentials-byok.md)
- Hook points: [app-bundles-v1.md](../app-bundles-v1.md)
