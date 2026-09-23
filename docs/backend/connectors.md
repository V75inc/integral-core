# Adding connectors

How to add a package to the **Connector library** (Settings → Connectors →
Add Connector). Architecture: [ADR-010](adr/010-connector-subsystem-architecture.md).
MCP client mount: [ADR-009](adr/009-mcp-as-connector.md). Invariants:
[INVARIANTS.md](../INVARIANTS.md) I-CON-01…05.

**Vetting gate:** a connector is in the product only if it has a YAML file in
`backend/app/connectors/catalog/`. The Official MCP Registry is not a browse
source.

## Decision tree

```
Need durable mirrored Entries in the graph?     → Native adapter (kind: sync).
Need live RPC to an external MCP tool surface?  → MCP mount (kind: mcp).
Need both?                                      → Two catalog entries for now
                                                  (hybrid reserved, not v1).
Proxy an HTTP API without mirroring or MCP?     → Rejected — pick native or MCP.
```

| Kind | Catalog `kind` | Runtime | Instance `subclass_slug` |
|------|----------------|---------|--------------------------|
| Native adapter | `sync` | `SyncConnector.sync_pull` → Entries | `@register_sync_connector` key |
| MCP mount | `mcp` | ADR-009 discover + `tools/call` | `"mcp"` |

## Catalog YAML keys

File: `backend/app/connectors/catalog/<slug>.yaml`

`slug` **must** equal the filename stem (`gmail.yaml` → `slug: gmail`). The
loader (`catalog_loader.py`) raises at import/test time otherwise.

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

## APIs the library uses

| Method | Path | Role |
|--------|------|------|
| GET | `/agentive/connectors/catalog` | List vetted packages (no secrets) |
| GET | `/agentive/connectors/catalog/{slug}` | Install preview |
| POST | `/agentive/connectors/catalog/{slug}/install` | `{ secrets }` → `action: oauth \| created` |

Workspace admin + `X-Integral-Scope: ws:<id>` required. Schemas:
`CatalogEntry`, `CatalogInstallRequest`, `CatalogInstallResponse` in
`backend/app/schemas/agentive/connectors.py`.

## Seeds in-repo today

| Slug | Kind | Auth |
|------|------|------|
| `gmail` | native sync | oauth2 (`client_id`, `client_secret` optional) |
| `quickbooks` | native sync | oauth2 (+ optional `environment`) |
| `github_issues` | native sync | env (`owner`, `repo`; token from process env) |
| `calendly` | MCP HTTP | none |
| `notion` | MCP HTTP | none (interactive OAuth on 401) |
| `google_drive` | MCP HTTP | oauth2 (Google sign-in; Drive MCP does not 401) |
| `google_gmail` | MCP HTTP | oauth2 (Gmail MCP live tools; not the native sync connector) |
| `google_sheets` | MCP HTTP | oauth2 (Google sign-in; Sheets MCP does not 401) |
| `quickbooks_mcp` | MCP stdio (`npx`) | oauth2 (Intuit popup; same app as native QuickBooks) |
