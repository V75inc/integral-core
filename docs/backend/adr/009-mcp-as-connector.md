# ADR 009 — External MCP servers as Integral connectors

**Status:** Accepted
**Date:** 2026-08
**Renumbered:** 2026-09 from ADR-005 (two records folded: capability framing
+ adapter spec) so the number does not collide with
[ADR-005 single-worker](005-single-worker-until-shared-turn-state.md).

Parent architecture: [ADR-010](010-connector-subsystem-architecture.md).
Inbound MCP (external agents calling Integral) remains
[ADR-003](003-singular-resident-harness.md) / [BYOA.md](../../product/BYOA.md).

## Context

Integral already exposes an **inbound** MCP server so external agents can call
substrate tools (`backend/app/agentive/mcp/server.py`). The inverse — mounting
an *external* MCP server so the **resident** can call *its* tools — did not
exist beyond a chat-vendor stub (`McpStubConnector`).

Connectors today are **mirror-model** (`SyncConnector`): they pull external
records into the graph as Entries. Product language (“mirror, not proxy”)
applies to **knowledge**. MCP tools are **capabilities** (actions), not a
second query path around the graph. Treating a remote MCP server as a
SyncConnector would be the wrong pillar: there is nothing to materialize on
`sync_pull`.

The workspace tool surface is in-process (`services/hooks/registry.py`).
Settings-gated App install once skipped `register_bundle_on_install` on
`finalize_install`, so tools stayed inert until restart. MCP mounts must not
repeat that class of bug.

## Decision

1. **MCP servers mount as Connectors**, kind `mcp`, `subclass_slug="mcp"`.
   Reuse the `Connector` node. Do **not** implement `SyncConnector.sync_pull`
   for this path. SyncConnector is pull→Entry mirror; external MCP tools are
   live RPC. They share the Connector Node / OWNS edge / policy-at-create
   pattern.

2. **Hot registration into the workspace tool surface.** Discovered remote
   tools register via `register_workspace_tools(workspace_id, f"mcp:{id}", …)`
   — the same in-process registration class used by App
   `finalize_install` → `register_bundle_on_install`. No process restart is
   required. Boot calls `rehydrate_mcp_connectors()` alongside
   `rehydrate_all_installed_bundles()`. The resident `get_tools` surface is
   the static manifest catalogue **plus** workspace tools for the bound
   scope (bundle-local and `mcp__*` keys). Dispatch of `mcp__*` names does
   not require a `tool_manifest.yaml` entry.

3. **Names.** Workspace keys are `mcp__{connector_short_id}__{remote_name}`
   so they never collide with `integral_*`. Global `tool_manifest.yaml` /
   Integral’s outbound MCP catalogue are **not** mutated for remote tools.

4. **Per-workspace.** Additive `Connector.workspace_id` (cache) plus
   structural `Workspace —HAS_CONNECTOR→ Connector` (I-GRAPH-01). Keep
   `User —OWNS→ Connector`.

5. **Policy.** MCP connectors materialize a connector-subject Policy with
   `tool.invoke` + `connector.read` (I-CON-04 fail-closed). Invokes also
   evaluate the caller (`tool.invoke` on
   `Resource(kind="connector", scope="connector:<id>")`). Human owners are
   allowed via the existing default-human `connector:` scope branch in
   `policy_engine`; agents need an explicit Policy.

6. **Credentials.** Transport config + secrets live in `Connector.auth_state`
   (`url`, `headers?`, `transport`, `command`/`args`/`env` for stdio, plus a
   nested `oauth` dict when the remote requires OAuth). Wire responses redact
   `env` / `headers` values (Gmail/QB redaction idiom). GET responses redact
   MCP `auth_state` to `url`, `transport`, and `oauth.status`. Never emit
   `auth_state` in ChangeEvent snapshots.

7. **Transports v1.** `stdio` (primary, CI fixture) and `streamable_http`
   (secondary). Acceptance uses an in-repo stdio echo server under
   `backend/tests/fixtures/mcp_echo_server.py`.

8. **Health.** Additive scalars `health_status` (`ok|degraded|error|unknown`),
   `last_error`, `last_health_at`. Updated on discover/invoke failure.
   Surfaced on `ConnectorResponse`. Discovered tool specs persist on
   `Connector.discovered_tools` (never in `auth_state`).

9. **Forbidden.** Do not revive A2A wrappers, or place the proxy under
   `app.packages/*/tools` (bundle facade / ADR-003). Chat `McpStubConnector`
   remains a vendor-routing proof; tool mount is a separate surface.

## Consequences

- Product “mirror not proxy” is unchanged for knowledge connectors. MCP
  mounts must not be used as a live query substitute for graph retrieval.
- Encrypting `auth_state` at rest is a follow-up (BYOK Object pattern).
- New additive Connector fields: `workspace_id`, `health_status`,
  `last_error`, `last_health_at`, `connection_mode` (`"per_user"` | `"shared"`),
  and operator `label`.
- **Connector Scoping & Resolution (Amendment 2026-09):**
  - Personal workspaces are strictly per-user (`connection_mode="per_user"`).
  - Collaborative workspaces support `"shared"` connections (admin install only;
    members get invocation rights; actions author as the shared identity).
  - Multi-row canonical tool deduplication (`mcp:canonical:<slug>`): hot tools
    are registered once under canonical keys with per-request actor resolution
    (caller's personal row takes precedence over the shared row).
- **Custom MCP Mounts:**
  - Supported via `POST /api/agentive/connectors/mcp/mount` with `streamable_http`
    or `stdio` (gated by `INTEGRAL_ALLOW_STDIO_MCP` / `DEBUG` / `TESTING`).
- Sync connectors (Gmail / QuickBooks / GitHub Issues) are unchanged.

## Alternatives considered

- **New node type `McpServer`.** Rejected; Connector is already the actor of
  record for policy, audit, and settings.
- **SyncConnector that no-ops `sync_pull`.** Rejected; wrong pillar and would
  trip the scheduler.
- **Overload `mcp_stub_connector` (chat vendor).** Rejected; that stub is an
  `AgentChatConnector` keyed on `AgentType`, not a workspace capability mount.
- **Bundle tools for MCP proxy.** Rejected; violates bundle facade / ADR-003.

## Cross-references

- Parent architecture: [ADR-010](010-connector-subsystem-architecture.md)
- Authoring: [connectors.md](../connectors.md)
- Invariants: [INVARIANTS.md](../../INVARIANTS.md) Phase 5 (I-CON-01…05)
