# ADR 018 — Drop-in connector packages

**Status:** Proposed. Not implemented.

## Context

Connectors are split across a YAML catalog, Custom MCP, native sync adapters
imported by name, the managed-channel wheel contract and a separate WhatsApp
stack. Each has its own install record, API and Settings path, and Core
branches on connector slugs. Adding a sync or API connector requires a Core
change; only channels have an extension contract, and it requires a
pip-installed wheel. A public marketplace is a stated eventual goal.

## Decision

1. A connector is a **package folder** with a `connector.yaml` manifest
   (`integral.connector/v1`), dropped into `integral-connectors/` or a root
   listed in `INTEGRAL_CONNECTOR_PATHS`. One registry admits packages from
   these roots and from Core's first-party package folder. Core stops
   importing connectors by name.
2. A package declares one or more capabilities — `actions`, `mcp`, `sync`,
   `channel` — and a binding (`workspace`/`personal`, cardinality). Each
   capability is handed to its existing execution lane; lanes stay separate.
3. Packages are **declarative first**. Three trust tiers: `declarative`
   (manifest only, admitted automatically when valid, Core executes all
   requests), `trusted` (in-process Python behind a narrow context, signature
   required, loaded at startup) and `remote` (out-of-process adapter over a
   signed protocol). Untrusted code never runs in the Core process.
4. Every kind uses **one installation record**, **one management API** and
   **one Settings projection**. Add and Configure are rendered from the
   manifest by Core's shared settings renderer, extended once for `secret`,
   `url` and OAuth. Secrets are encrypted handles that are never read back.
5. Admins **Rescan** to admit declarative changes without restart. Rejected
   packages are listed with a reason; installed connectors whose package
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
