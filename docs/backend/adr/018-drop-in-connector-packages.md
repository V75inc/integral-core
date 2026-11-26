# ADR 018 — Drop-in connector packages

**Status:** Implemented for declarative drop-ins and Core-owned integrations.

## Context

Connectors are split across a YAML catalog, Custom MCP, native sync adapters
imported by name, the managed-channel wheel contract and a separate WhatsApp
stack. Each has its own install record, API and Settings path, and Core
branches on connector slugs. Adding a sync or API connector requires a Core
change; only channels have an extension contract, and it requires a
pip-installed wheel.

## Decision

1. A connector is a **package folder** with a `connector.yaml` manifest
   (`integral.connector/v1`), dropped into `integral-connectors/` or a root
   listed in `INTEGRAL_CONNECTOR_PATHS`. One registry admits packages from
   these roots and from Core's first-party package folder. Core resolves
   first-party executable integrations from fixed startup code.
2. A package declares one or more capabilities — `actions`, `mcp`, `sync`,
   `channel` — and a binding (`workspace`/`personal`, cardinality). Each
   capability is handed to its existing execution lane. Personal installs
   target the caller’s Personal Workspace. Add requires an explicit choice when
   both scopes are available. Existing installations retain their binding.
3. Operator roots admit only declarative manifests. Core runs declared
   actions, HTTP MCP, sync and webhook steps through the existing guarded
   runtime. First-party integrations ship as reviewed Core source.
   A fixed startup mapping wires the sync and
   channel adapters, and only bundled Core definitions select stdio commands.
   Operator roots and saved installation snapshots cannot select code.
4. Every kind uses **one installation record**, **one management API** and
   **one Settings projection**. Add and Configure are rendered from the
   manifest by Core's shared settings renderer, extended once for `secret`,
   `url` and OAuth. Secrets are encrypted handles that are never read back.
5. Admins **Rescan** to admit declarative changes without restart. When a scan rejects a package, the result explains why. Rejected packages
   stay out of the Add directory; installed connectors whose package
   becomes unavailable remain visible with Disable and Remove only.

The manifest schema is
[`integral-connector-v1.schema.json`](../../platform/schemas/integral-connector-v1.schema.json);
the developer guide is [connector packages v1](../../platform/connector-extension-v1.md).

## Consequences

- Extends ADR 009 and ADR 010: MCP stays an outbound connector capability and
  lanes keep their trust perimeters; registration, installation and
  presentation become uniform.
- Auto-admission of declarative packages means any file-system writer to a
  connector root can offer a connector to admins. Protection comes from
  Core-executed requests, the outbound URL guard, secret injection only via
  `auth`, review for writes and unlisted MCP tools, and admin-only Add for
  workspace scope. Deployments must restrict write access to connector roots.
- Existing connectors migrate to first-party packages with preserved ids,
  credentials, bindings, cursors and grants. Sync tokens must be encrypted
  before migration.
- Under the single-worker baseline (ADR 005), Rescan affects one process;
  multi-worker deployments need a shared admission record before relying on
  it.
- Substrate work (installation node, registry, renderer extension) requires
  reviewed plans against the invariants, phased as in the guide.

## Native integrations

Developers adding native connector code can fork Core and build it with their
changes. The drop-in contract accepts declarative data from operator roots.
`integral init` creates the connector directory beside `integral-apps/`.
The managed launcher discovers the same sibling when starting an existing
project with `integral up --apps`.
