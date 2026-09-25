# Adding connectors & The Connector Abstraction

How to integrate external systems into Integral and add packages to the
**Connector library** (Settings → Connectors → Add Connector).

Architecture: [ADR-010](adr/010-connector-subsystem-architecture.md).
MCP client mount: [ADR-009](adr/009-mcp-as-connector.md).
Invariants: [INVARIANTS.md](../INVARIANTS.md) I-CON-01…05.

---

## 1. The Connector Abstraction

In Integral, **every external service integration is unified under a single
graph abstraction: the `Connector` node** (`class Connector(Node)` in
`backend/app/agentive/nodes.py`).

Whether integrating an external **RESTful API** (e.g. GitHub, Gmail, Jira,
QuickBooks), a **GraphQL endpoint**, or an **MCP (Model Context Protocol)
server** (e.g. Notion, Calendly, Google Drive, internal microservices), you do
**not** invent custom persistence models, custom background threads, or ad-hoc
FastAPI proxy endpoints.

### What the Abstraction Provides Out of the Box

By building on top of the `Connector` abstraction, an integration
automatically receives:

1. **Graph Lifecycle & Tenancy:**
   - Durable representation as a `Connector` Node in the jvspatial graph.
   - Ownership anchored to the installer (`User —OWNS→ Connector`).
   - Denormalized workspace scope (`workspace_id`).
   - Optional Track bindings (`Connector —IS_CONNECTED_TO→ Track`) for data sync.
2. **Tenancy Scoping (`connection_mode`):**
   - **`per_user`**: Connection credentials belong solely to the user who
     installed it (required in personal workspaces).
   - **`shared`**: Workspace-wide connection installed by a workspace admin,
     allowing all members to invoke tools or sync data under shared credentials.
3. **Credential & Secret Protection:**
   - Structured `auth_state` sealed at rest and strictly redacted on wire
     responses (`mcp_safe_auth_state`, `ConnectorResponse`). Plaintext tokens
     are never leaked into graph audit snapshots or ChangeEvents.
4. **Health & Status Lifecycle:**
   - Real-time `health_status` (`ok`, `degraded`, `error`, `unknown`),
     `last_error`, and `last_health_at` timestamps.
5. **Access Control & Policy Gating:**
   - Governed by Integral's Policy Engine (I-CON-04). Automatically attached
     Policy records grant necessary PolicyActions (`connector.sync`,
     `entry.create`, `entry.update` for sync; `tool.invoke`, `connector.read`
     for MCP).
6. **Unified UI & Settings Surface:**
   - Browse vetted integrations in the Catalog, inspect connected tools, manage
     health status, test connectivity, and configure connection labels directly
     under **Settings → Connectors**.

---

## 2. Decision Tree & Integration Paradigms

```
Need durable mirrored Entries in the graph?
   └──> Native RESTful Sync Adapter (kind: sync, SyncConnector).

Need live tools / RPC for AI agents?
   ├──> External service already speaks MCP?
   │     └──> MCP Mount (kind: mcp).
   └──> External service is a RESTful API?
         └──> Wrap REST endpoints in a lightweight MCP server,
              then mount as MCP Connector (kind: mcp).

Need both (e.g., Jira issue mirror + Jira action tools)?
   └──> Two catalog entries (one `kind: sync`, one `kind: mcp`).

Proxy an external HTTP API without mirroring or MCP?
   └──> REJECTED — pick Native Sync or MCP. Do not write raw API endpoints.
```

### Comparative Architecture

| Dimension | Native Sync Adapter (`kind: sync`) | MCP Tool Connector (`kind: mcp`) |
|-----------|-----------------------------------|----------------------------------|
| **Primary Goal** | Knowledge Mirroring | Action Execution & Live Query |
| **Data Shape** | Durable graph `Entry` nodes in Tracks | Ephemeral tool call results / RPC |
| **Runtime Interface** | Subclass `SyncConnector` (`sync_pull`, `to_entry`) | MCP client (`mcp_mount` / `mcp_adapter`) |
| **Transport** | RESTful / GraphQL HTTP via `httpx` | `streamable_http` or `stdio` |
| **Catalog `kind`** | `sync` | `mcp` |
| **Instance `subclass_slug`** | Registry slug (`@register_sync_connector`) | `"mcp"` |
| **Scheduler** | Sync scheduler ticks periodically | On-demand agent / resident dispatch |
| **Hooks** | `connector.dedup`, `connector.auto_link` | None (ephemeral) |
| **Write Approvals** | Conflict policy (`last_write_wins`, `manual_resolve`) | Human-in-the-loop approval (`write: true`) |

---

## 3. Catalog Manifest Specification

Vetted catalog packages live in `backend/app/connectors/catalog/<slug>.yaml`.
`slug` **must** equal the filename stem (`gmail.yaml` → `slug: gmail`).

| Key | Required | Values / notes |
|-----|----------|----------------|
| `slug` | yes | `[a-z0-9_]+`; global (I-CON-03 for sync) |
| `display_name` | yes | Library card title |
| `description` | no | Card body |
| `category` | yes | `native` \| `mcp_server` \| `mcp_package` (filter chip only) |
| `kind` | yes | `sync` \| `mcp` |
| `icon` | no | Frontend brand key; defaults to `slug`; unknown → generic MCP mark |
| `auth.type` | yes | `oauth2` \| `api_key` \| `env` \| `none` \| `headers` |
| `auth.fields[]` | no | Install-sheet inputs (see below) |
| `implementation.sync_connector_class` | sync | Must match `@register_sync_connector("<slug>")` |
| `transport` | MCP | `streamable_http` (hosted) or `stdio` (local process) |
| `url` | HTTP MCP | Remote MCP endpoint |
| `command` / `args` | stdio MCP | Process to spawn |
| `env_defaults` | stdio MCP | Static process env merged before install-sheet secrets (e.g. read-only `QUICKBOOKS_DISABLE_*`) |
| `oauth` | MCP `auth.type: oauth2` | Pre-registered OAuth: `authorization_endpoint`, `token_endpoint`, `scopes[]`, optional `extra_authorize_params` (e.g. Google `access_type=offline`) |

### `auth.fields[]`

Posted as `POST /agentive/connectors/catalog/{slug}/install` body
`{ "secrets": { "<name>": "<value>", … } }`. Empty optional fields are omitted.

| Field | Type | Notes |
|-------|------|-------|
| `name` | string | Key in `secrets` and, for native, `Connector.auth_state` |
| `label` | string | Install sheet label |
| `secret` | bool | Password input when `true` |
| `required` | bool | Default `true`. OAuth client id/secret are often `required: false` so server env can fill in |

**Where secrets go at install:**

| `auth.type` | Native (`kind: sync`) | MCP (`kind: mcp`) |
|-------------|----------------------|-------------------|
| `oauth2` | Native OAuth start (see below). Extra fields (e.g. `environment`) land on `auth_state` | Catalog `oauth:` block (Google Drive). Use when the MCP does **not** 401 on initialize |
| `env` / `api_key` | Merged into `auth_state` | Passed as process `env` on stdio mount |
| `headers` | Merged into `auth_state` | HTTP `headers` on mount |
| `none` | No extra fields | HTTP URL only; remote may still demand OAuth |

Wire responses redact tokens / headers (`mcp_safe_auth_state` and native
helpers). Do not put secrets in ChangeEvent snapshots.

## Native adapters (`kind: sync`)

Mirror-model: pull external records into the graph. Do **not** implement live
RPC here.

### Interface

`SyncConnector` in `backend/app/services/connectors/base.py`:

```python
class SyncConnector:
    slug: str = ""
    conflict_policy: ConflictPolicy = "last_write_wins"  # last_write_wins | manual_resolve | mirror_only

    async def sync_pull(self, *, connector) -> AsyncIterator[ExternalRecord]:
        ...

    def to_entry(self, record: ExternalRecord) -> MaterializedEntry:
        ...

    def idempotency_key_for(self, record: ExternalRecord) -> str:
        # default SHA-256(f"{slug}:{external_id}")
        ...
```

Handoff types:

- `ExternalRecord` — `external_id`, `payload`, optional `updated_at`
- `MaterializedEntry` — `title`, `body`, `entry_type_key`, `tags`,
  `custom_fields`, optional `external_updated_at`

The subclass **must not** write `provenance` (I-CON-01). `sync_runtime` stamps
`source="connector"` and `source_id="<connector_id>:<external_id>"`.

Register with the global slug (I-CON-03):

```python
@register_sync_connector("my_source")
class MySourceConnector(SyncConnector):
    slug = "my_source"
```

Side-effect import the module from
`backend/app/agentive/connectors/__init__.py` so the decorator runs when
agentive loads (I-CON-05). Executable code stays in agentive; the ABC +
registry live in `app/services/connectors/` so non-jvagent boots can still
import the types.

### Checklist

1. **YAML** — `backend/app/connectors/catalog/<slug>.yaml` with `kind: sync`,
   `category: native`, `implementation.sync_connector_class` = registry slug.
2. **Class** — `backend/app/agentive/connectors/<slug>.py` + import in
   `__init__.py`.
3. **Operational Model** (if you materialize a new EntryType) —
   `backend/app/packages/<cp-slug>/operational-model.yaml`. Merge onto the bound Track
   (install does not auto-merge yet). See
   [operational-model-authoring-and-library.md](operational-model-authoring-and-library.md).
4. **Track binding** — operator attaches `IS_CONNECTED_TO` after install
   (I-CON-02). Mapping YAML lives on that edge, not on the catalog file.
5. **Icon** — map `icon` in
   `frontend/src/features/settings/connectors/ConnectorBrandIcon.tsx`
   (`@thesvg/react/<brand>`). Unmapped icons use the generic MCP SVG.
6. **Tests** — extend `backend/tests/test_connector_catalog.py` slug set;
   add connector unit tests next to existing `gmail` / `github_issues` /
   `quickbooks` tests.
7. **OAuth (only if `auth.type: oauth2`)** — catalog install currently
   dispatches to **slug-specific** consent builders (`gmail`, `quickbooks` in
   `_catalog_install_oauth`). A new OAuth native package needs a matching
   handler (consent URL + callback) before the library Add button will work.
   Optional `client_id` / `client_secret` fields let the operator override
   server env.

Reference: `github_issues.yaml` + `github_issues.py` (env token, never persist
`GITHUB_TOKEN` to `auth_state`).

## MCP mounts (`kind: mcp`)

Live tools, not mirrored Entries. Tools register as
`mcp__{connector_short_id}__{remote_name}` (ADR-009). No
`tool_manifest.yaml` entry.

### Interface (runtime)

You do **not** subclass `SyncConnector`. Catalog install calls:

| `transport` | Function | Typical `auth` |
|-------------|----------|----------------|
| `streamable_http` | `mcp_adapter.mount_mcp_connector(...)` or pre-registered OAuth | `none`/`headers` (401 → DCR); `oauth2` skips the probe |
| `stdio` | `mcp_mount.mount_mcp_connector(..., command, args, env?)` | `env` / `api_key` / Intuit `oauth2` (QuickBooks MCP) |

The API process must have the spawn command on `PATH` (`npx` / `node` for
npm packages). The backend Docker image is Python-only and does **not**
include Node — stdio packages are for a Node-equipped host unless the
image is extended.

**QuickBooks MCP** uses Integral's native Intuit OAuth popup (same Client
ID/secret and redirect URI as native QuickBooks:
`/connectors/quickbooks/callback`). After consent, the callback writes
credentials to `backend/.data/mcp-stdio/<workspace>/<uuid>.env` and
persists only `QUICKBOOKS_TOKEN_STORE_PATH` (plus `QUICKBOOKS_ENVIRONMENT`)
on `auth_state.env`. Do not copy MCP tools or write scopes onto the native
`quickbooks` sync connector.

After mount: `tools/list`, workspace tool table
`register_workspace_tools(workspace_id, f"mcp:{id}", …)`, health on the
Connector. Boot `rehydrate_mcp_connectors()` restores sessions.

HTTP OAuth has two install paths:

1. **Probe-driven (Notion-style)** — catalog `auth.type: none`. The adapter
   probes the URL; a 401 starts MCP OAuth discovery + DCR. Install returns
   `action: "oauth"` + `consent_url`.
2. **Pre-registered (Google Workspace MCP)** — catalog `auth.type: oauth2`
   plus an `oauth:` block. Drive / Gmail / Sheets MCP answer unauthenticated
   `initialize` with 200, so a 401 probe never fires. Install starts Google
   authorization **before** treating the connector as connected. Optional
   `client_id` / `client_secret` fields fall back to
   `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`, then the Gmail
   Google OAuth app (`GMAIL_OAUTH_*`). Add the product scopes and the MCP
   callback URI on that OAuth client. Do **not** add MCP scopes (Drive,
   Sheets, or `gmail.compose`) to the native Gmail sync connector.

   On the **same** Google Cloud project that owns the OAuth client, enable
   the product API **and** the matching `*mcp.googleapis.com` service or
   authenticated calls return 403:

   ```bash
   gcloud services enable \
     drive.googleapis.com drivemcp.googleapis.com \
     gmail.googleapis.com gmailmcp.googleapis.com \
     sheets.googleapis.com sheetsmcp.googleapis.com \
     --project=PROJECT_ID
   ```

   If enabling a `*mcp.googleapis.com` service is refused, the project is
   not in Google's Workspace MCP preview allowlist.

Callback (both paths): `/settings/connectors/mcp/oauth/callback` (frontend) →
`POST /agentive/connectors/mcp/oauth/callback`. Tokens live under
`auth_state.oauth` and are redacted on GET.

### Checklist

1. **YAML** — `kind: mcp`, `category: mcp_server` (hosted) or `mcp_package`
   (stdio/npx). Set `transport` + `url` **or** `command`/`args`.
2. **No Python adapter** unless you are changing mount/OAuth itself.
3. **Icon** — same `ConnectorBrandIcon` map as native.
4. **Tests** — catalog loader assertions for slug / url / transport;
   OAuth coverage in `backend/tests/test_mcp_oauth.py` if the server is
   OAuth.

References: `notion.yaml` (`https://mcp.notion.com/mcp`), `calendly.yaml`
(`https://mcp.calendly.com/`), `google_drive.yaml`
(`https://drivemcp.googleapis.com/mcp/v1`), `google_gmail.yaml`
(`https://gmailmcp.googleapis.com/mcp/v1`), `google_sheets.yaml`
(`https://sheetsmcp.googleapis.com/mcp/v1`), `quickbooks_mcp.yaml`
(stdio `npx` of Intuit's local server).

---

## 4. Bridging External RESTful Services as MCP Tool Connectors

A frequent requirement is connecting an **external RESTful API** (e.g. Stripe,
Jira, Zendesk, Salesforce, or an internal microservice) to Integral so AI
agents can execute actions or query data on demand.

### The Wrong Way: Reinventing the Wheel
- Writing custom FastAPI routes in `backend/app/api/` that proxy requests to the
  external REST API.
- Hardcoding custom tool wrappers or ad-hoc background polling scripts.
- Bypassing the Policy Engine, credential redaction, and workspace scoping.

### The Right Way: The REST-to-MCP Pattern
Because Integral's `Connector` abstraction natively speaks MCP, the cleanest,
fastest, and most maintainable way to expose any RESTful service to agents is to
**wrap the REST API in a lightweight MCP server**:

```python
# Example: Wrapping a REST API using FastMCP (Python)
from mcp.server.fastmcp import FastMCP
import httpx
import os

mcp = FastMCP("Support Ticketing Service")
API_BASE = "https://api.example.com/v1"
API_KEY = os.environ.get("SERVICE_API_KEY", "")

@mcp.tool()
async def search_tickets(query: str, status: str = "open") -> list[dict]:
    """Search tickets by keyword and status."""
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{API_BASE}/tickets",
            params={"q": query, "status": status},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        res.raise_for_status()
        return res.json().get("items", [])

@mcp.tool()
async def create_ticket(title: str, description: str, priority: str = "normal") -> dict:
    """Create a new support ticket. Note: agents may require human approval."""
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{API_BASE}/tickets",
            json={"title": title, "description": description, "priority": priority},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        res.raise_for_status()
        return res.json()
```

Once wrapped, mount the MCP server in Integral via either:
1. **Catalog YAML (`backend/app/connectors/catalog/<slug>.yaml`)**: For
   reusable, vetted packages with pre-configured headers or OAuth.
2. **Custom MCP Mount Modal in the UI**: For rapid internal or developer
   testing directly from **Settings → Connectors → Custom MCP Server**.

Integral automatically provides:
- Live JSON Schema validation of tool parameters.
- Multi-tenant workspace scoping (`per_user` vs `shared`).
- Secure credential redaction (`mcp_safe_auth_state`).
- Human-in-the-loop approval confirmation for write actions (`write: true`).
- Real-time tool inspection in the Settings UI.

---

## 5. Connection Scoping & Tenancy (`connection_mode`)

Integral connectors support two distinct connection modes to accommodate both
individual productivity and team-wide operations:

| Mode | `connection_mode` | Target Workspace | Authority Required | Behavior |
|------|-------------------|------------------|--------------------|----------|
| **Per-User** | `"per_user"` (default) | Personal & Collaborative | Any workspace member | Credentials belong exclusively to the installer. Used only when that user interacts with tools. |
| **Shared** | `"shared"` | Collaborative workspaces only | Workspace Admin / Owner | Single connection shared by all workspace members. Tools execute using shared credentials. |

### Workspace Constraints & Security Rules

1. **Personal Workspaces:**
   - Personal workspaces (`is_personal=True`) are strictly single-tenant.
   - Attempting to install or mount a connector with `connection_mode="shared"`
     is rejected with **HTTP 400 Bad Request**.
2. **Collaborative Workspaces:**
   - Members can install `per_user` connectors anytime.
   - Installing or mounting a `shared` connector requires **workspace admin or
     owner role**. Member attempts are rejected with **HTTP 403 Forbidden**.
   - Only one `shared` connector row is permitted per connector slug (e.g. one
     shared Notion row per workspace). Duplicate shared installations are
     rejected with **HTTP 409 Conflict**.
   - Members in collaborative workspaces have read and invoke permissions on
     shared connectors (`connector.read` and `tool.invoke` via Policy Engine),
     while update, delete, and sync triggers remain restricted to admins.

### Dynamic Caller Resolution Precedence

When an agent or user invokes a connector tool (e.g. `mcp__notion__search`),
the runtime resolves which connector row to use via
`resolve_connector_row(rows, caller_user_id)`:

1. **Personal Row Precedence:** If the caller has their own active `per_user`
   connector for that slug, it is **always selected first**. This ensures
   personal permissions, quotas, and audit identities take precedence.
2. **Shared Row Fallback:** If the caller has no personal row, the active
   `shared` connector row in the workspace is selected. Tools execute using the
   shared credentials while recording the invoking user in audit logs.

### Multi-Row Canonical Tool Deduplication

To prevent duplicate tool registrations from cluttering agent context when
multiple users install the same connector:
- Tools are registered under a single canonical workspace key:
  `mcp:canonical:<slug>`.
- A refcounter tracks how many active rows exist for that slug.
- Hot dispatch dynamically resolves credentials on each invocation.
- When the last connector row for a slug is deleted, the canonical tools are
  cleanly unregistered.

---

## 6. Custom MCP Server Mounting

In addition to vetted catalog packages, operators and developers can mount
ad-hoc MCP servers directly via:

- **UI:** Settings → Connectors → Click **Custom MCP Server** card.
- **API:** `POST /api/agentive/connectors/mcp/mount`

### Supported Transports

1. **Streamable HTTP (`streamable_http`):**
   - Connects to any remote, hosted MCP server over HTTP/HTTPS with Server-Sent
     Events (SSE) streaming.
   - Validates the endpoint upfront, probing tools and ensuring availability
     before creating the graph node.
   - Supports custom authentication headers (e.g. `Authorization: Bearer ...`).
2. **Standard I/O (`stdio`):**
   - Spawns a local CLI process (e.g. `npx -y @modelcontextprotocol/server-sqlite`).
   - Gated by environment variable `INTEGRAL_ALLOW_STDIO_MCP=1` (or `DEBUG=true` /
     `TESTING=1`). Production environments without this flag reject stdio mounts
     with **HTTP 403 Forbidden** for security isolation.

---

## 7. Common Pitfalls & Anti-Patterns

To keep Integral maintainable and avoid reinventing the wheel, observe these
rules:

1. **NEVER create raw FastAPI endpoints to talk to external APIs:**
   - Do not add `@endpoint` routes in `backend/app/api/` that make ad-hoc
     outbound HTTP calls.
   - **Correct:** Use `SyncConnector` for mirrored data, or wrap the API as an
     MCP tool for live agent interaction.
2. **NEVER run unmanaged background polling loops:**
   - Do not spawn custom `asyncio.create_task` loops to fetch data on intervals.
   - **Correct:** Implement `sync_pull` on a `SyncConnector`; the sync scheduler
     manages execution, backoff, and cursors.
3. **NEVER persist plaintext credentials:**
   - Do not write tokens to unencrypted node fields, `context` dictionaries, or
     log statements.
   - **Correct:** Store secrets in `Connector.auth_state`. Redact on wire via
     `mcp_safe_auth_state`.
4. **NEVER create detached graph nodes (I-GRAPH-01):**
   - Every `Connector` MUST be rooted with `User —OWNS→ Connector`.
   - Every track sync binding MUST be an explicit `Connector —IS_CONNECTED_TO→ Track`
     edge with mapping metadata.

---

## 8. APIs the Library & Settings Surface Uses

| Method | Path | Role |
|--------|------|------|
| GET | `/agentive/connectors/catalog` | List vetted packages (no secrets) |
| GET | `/agentive/connectors/catalog/{slug}` | Install preview |
| POST | `/agentive/connectors/catalog/{slug}/install` | Install from catalog (`{ secrets, connection_mode, label }`) |
| POST | `/agentive/connectors/mcp/mount` | Mount custom HTTP/stdio MCP server |
| GET | `/agentive/connectors/{id}/tools` | List registered tools and parameter schemas for a connector |
| POST | `/agentive/connectors/mcp/refresh` | Refresh tools for an active MCP connector |
| POST | `/agentive/connectors/mcp/oauth/callback` | OAuth callback for interactive MCP authentication |

Workspace admin + `X-Integral-Scope: ws:<id>` required for catalog install and
shared mounts. Schemas: `CatalogEntry`, `CatalogInstallRequest`,
`CatalogInstallResponse`, `ConnectorToolsResponse` in
`backend/app/schemas/agentive/connectors.py`.

---

## 9. Seeds in-Repo Today

| Slug | Kind | Auth | Scoping Supported |
|------|------|------|-------------------|
| `gmail` | native sync | oauth2 (`client_id`, `client_secret` optional) | Per-user / Shared |
| `quickbooks` | native sync | oauth2 (+ optional `environment`) | Per-user / Shared |
| `github_issues` | native sync | env (`owner`, `repo`; token from process env) | Per-user / Shared |
| `calendly` | MCP HTTP | none | Per-user / Shared |
| `notion` | MCP HTTP | none (interactive OAuth on 401) | Per-user / Shared |
| `google_drive` | MCP HTTP | oauth2 (Google sign-in; Drive MCP does not 401) | Per-user / Shared |
| `google_gmail` | MCP HTTP | oauth2 (Gmail MCP live tools; not native sync) | Per-user / Shared |
| `google_sheets` | MCP HTTP | oauth2 (Google sign-in; Sheets MCP does not 401) | Per-user / Shared |
| `quickbooks_mcp` | MCP stdio (`npx`) | oauth2 (Intuit popup; same app as native) | Per-user / Shared |

