# Integral connector packages v1

> **Status: implemented.** This guide covers
> operator-controlled declarative drop-ins and Core-owned first-party connectors.

A connector gives Integral a governed path to an external system. You build
one by dropping a folder with a `connector.yaml` manifest into a connector
root. Integral validates and admits it, lists it in **Settings → Connectors**,
and renders its Add and Configure forms from the manifest. No Core or
frontend code changes are needed.

The normative manifest definition is the JSON Schema
[`schemas/integral-connector-v1.schema.json`](schemas/integral-connector-v1.schema.json).
This guide explains it; when they disagree, the schema wins.
After editing the schema, run `make connector-schema` to refresh the runtime
copy used by wheels and containers. The wheel gate checks that the copies
match.

## Concepts

- **Package** — the folder you drop in: manifest, README and optional icon. Identified by a stable `id`.
- **Capability** — what the connector does. A package declares one or more:
  - `actions` — outbound API calls exposed to the agent as tools;
  - `mcp` — an external MCP server whose tools the agent can call;
  - `sync` — pull external records into a Track as Entries;
  - `channel` — inbound and outbound messaging with people outside Integral.
- **Binding** — who an installation belongs to (`workspace` or `personal`)
  and how many may exist. Binding is independent of capability.
- **Installation** — one durable instance of a package for a binding, created
  disabled by **Add**, holding configuration, encrypted secret handles, the
  installed package version and its own revision.
- **Trust tier** — `declarative` for operator drop-ins. `trusted` is reserved
  for bundled first-party definitions.

Capabilities run in separate execution lanes. An adapter in one lane never
grants access in another.

## Quickstart: a declarative API connector

From a fresh project:

```bash
integral init ./my-workspace
cd ./my-workspace
integral connectors new --kind actions acme_crm
```

For an existing project, run the same `connectors new` command from its root.
It creates the parent directory as needed and refuses to overwrite a package.
Replace the starter manifest at `integral-connectors/acme_crm/connector.yaml`
with the example below and set the URL to a service under your control.

```yaml
schema: integral.connector/v1
id: acme_crm
version: 1.0.0
publisher: acme
name: Acme CRM
description: Look up and create Acme CRM contacts.
icon: icon.svg
trust: { tier: declarative }
binding:
  scopes: [workspace, personal]
  cardinality: { workspace: one, personal: many }
configuration:
  - { key: base_url, label: Acme URL, type: url, required: true }
  - { key: api_key, label: API key, type: secret, required: true }
auth:
  type: api_key
  secret: api_key
  inject: { in: header, name: Authorization, prefix: "Bearer " }
http:
  base_url: "{{config.base_url}}"
capabilities:
  actions:
    - name: find_contact
      description: Find Acme contacts by name or email.
      effect: read
      input:
        type: object
        required: [query]
        properties: { query: { type: string, maxLength: 200 } }
      request:
        method: GET
        path: /api/contacts
        query: { q: "{{input.query}}" }
      output: { select: "$.items[*]", max_items: 25 }
    - name: create_contact
      description: Create an Acme contact.
      effect: write
      input:
        type: object
        required: [name]
        properties:
          name: { type: string, maxLength: 200 }
          email: { type: string, maxLength: 320 }
      request:
        method: POST
        path: /api/contacts
        body: { name: "{{input.name}}", email: "{{input.email}}" }
health:
  request: { method: GET, path: /api/ping }
```

Validate and start the local workspace:

```bash
integral connectors validate ./integral-connectors/acme_crm
integral up --apps ./integral-apps
```

1. As a collaborative workspace admin, open **Settings → Connectors** and
   select **Rescan**. Look for the connector under **Dropped-in packages**.
   Invalid packages show their validation reason.
2. Fill in the generated form and choose **Add**. The installation starts
   disabled. Choose **Configure** to make changes, then **Enable** to run the
   health probe. An authorized agent can call `find_contact` and propose
   `create_contact` for review.

## Package layout and discovery

```text
integral-connectors/
  acme_crm/                 # folder name == manifest id
    connector.yaml          # required
    README.md               # optional package notes
    icon.svg                # optional; PNG also accepted
```

The managed launcher starts host processes from the installation home. It
passes the absolute sibling `integral-connectors/` directory beside the
selected App directory to Core, followed by comma-separated extras from the
installation's `settings.env`. The launcher retains the App path across restarts.
An existing project can add its sibling connector directory while Core is
running and then use Rescan.

For an independently started Core, set `INTEGRAL_CONNECTOR_PATHS` to
comma-separated directories, or start Core with the project as its working
directory. In a container, mount the operator-controlled connector directory
read-only into the API container and use its container path in
`INTEGRAL_CONNECTOR_PATHS`. The local launcher needs no container mount.

Core reserves the IDs of first-party manifests that ship in the Core build.
Rescan reports duplicate operator IDs.

## Manifest reference

| Key | Required | Meaning |
|---|---|---|
| `schema` | yes | `integral.connector/v1`. |
| `id` | yes | Stable identifier, `^[a-z][a-z0-9_]{1,62}$`, equal to the folder name. Existing connector slugs keep their ids. |
| `version` | yes | Semantic version of this package. Installations record the version they were installed with. |
| `publisher` | yes | Package publisher handle. |
| `name`, `description` | yes | Library card text. |
| `icon` | no | Relative path inside the package. |
| `settings_surface` | no | Post-connect settings. `labels` opens mailbox label selection. `sync_interval` opens the sync schedule. The host chooses the surface from this field. |
| `core` | no | PEP 440 specifier of compatible Core versions. Admission rejects an invalid specifier and a specifier that does not include the running Core version. |
| `trust` | yes | Use `{tier: declarative}` in operator roots. The `trusted` tier is reserved for bundled Core definitions. |
| `binding` | yes | Allowed `scopes` and per-scope `cardinality`. Add offers only scopes the caller may install. |
| `configuration` | no | Form fields: `string`, `text`, `url`, `secret`, `number`, `boolean`, `select`. A field limited to `personal` is omitted from a workspace installation and cannot be saved there. |
| `auth` | no | `none`; `api_key` (inject a secret field as a header or query value); `oauth2` (static endpoints, optional `extra_authorize_params`, `client_authentication` of `client_secret_post` or `client_secret_basic`, and `redirect_env` / `client.id_env` / `client.secret_env` for deployment variables); `mcp_oauth` (MCP discovery and dynamic client registration); or `operator` (a credential from a deployment environment variable). |
| `http` | for declarative requests | `base_url` template, optional named `hosts` such as `sandbox` and `production`, plus timeout and response-size limits. |
| `capabilities` | yes | At least one of `actions`, `mcp`, `sync`, `channel`. |
| `health` | no | Readiness probe; **Enable** requires it to pass. |

### Templates

Request paths, query values, headers and body strings may reference
`{{config.<key>}}`, `{{input.<key>}}` (actions) and `{{cursor}}` (sync
pagination). Secrets are never templated: they reach a request only through
the `auth` block, so they cannot leak into URLs, logs or tool output. The host
of every declarative request comes from `http.base_url`; a `path` is always
relative. A path that climbs with `..`, `//`, or an encoded `..` is refused. The request timeout is at most 30 seconds. A response larger than
`max_response_bytes` is refused before it is parsed. The default is 262144
bytes and the maximum is 1048576.

## Capabilities

### actions — outbound API calls as agent tools

Each action becomes a tool named `<id>.<action>` in the workspace tool
registry for agents that can see the installation. Enable registers that
tool for the installation's workspace, and Disable or Remove takes it
away. A restart registers the tools for installations that are still
enabled. The call runs the installation recorded on the tool, not an id
supplied by the caller. A write called this way stages only the package review.

- `effect: read` must use `GET`. Core refuses a read that is not GET, including
  one stored on an installation. It runs after Core rechecks the caller, the
  installation, its scope and the current revision.
- `effect: write` always stages a review card. The request executes only
  after approval, with a fresh authorization check.
  Any other name is refused before a request URL is built.
- `input` is a JSON Schema for the tool arguments. Core rejects an argument
  that does not match it, before a read is sent or a write is staged.
  `output.select` chooses the result. `max_items` caps a list. The default is
  50 and the maximum is 200.

### mcp — external MCP server

```yaml
capabilities:
  mcp:
    transport: streamable_http
    url: "{{config.base_url}}/mcp"
    read_only_tools: [search_pages, get_page]
```

Tools omitted from `read_only_tools` stage review. Certify the list from a
live `tools/list`. Core ignores the remote server's `readOnlyHint` when
deciding whether to require review.
Servers that implement MCP authorization use `auth: { type: mcp_oauth }`.

`stdio` transport is allowed only in the trusted tier. The server process gets
only the environment its manifest declares, even when the connector has no
`env` map. `{config:}` reads the installation configuration, then `auth_state`.
`{secret:}` and `{oauth:}` read `auth_state` only. Secrets and tokens are
never written into templates. Keys the adapter writes after OAuth or a
token-store setup are listed in `generated_env`. Those names are allowed on
the process and are not taken from Core's environment. Boot rehydrate skips a
stdio OAuth package until one of its declared token-store paths is present.
That wait follows the manifest, not a catalog slug. Execution-selecting
names are still refused. An HTTP MCP server cannot declare `generated_env`:

```yaml
capabilities:
  mcp:
    transport: stdio
    command: [python, -m, acme_search_server]
    env:
      ACME_API_KEY: { secret: api_key }
      ACME_REGION: { config: region }
      ACME_ACCESS_TOKEN: { oauth: access_token }
      ACME_READ_ONLY: { value: "1" }
    generated_env: [ACME_TOKEN_STORE_PATH]
```

### sync — pull records into a Track

```yaml
capabilities:
  sync:
    entry_type: contact
    interval_seconds: 3600
    conflict_policy: mirror_only
    source:
      request: { method: GET, path: /api/contacts, query: { cursor: "{{cursor}}" } }
      items: "$.items"
      paginate: { next_cursor: "$.next", max_pages: 50 }
    map:
      external_id: "$.id"
      title: "$.name"
      updated_at: "$.updated_at"
      fields: { email: "$.email" }
```

When `paginate` omits `max_pages`, Core stops after 50 pages. A larger value stops at 1000. A source without `paginate` is one page.

The first-party package `example_contacts` declares this kind of `source` and `map`. A new package installation of it runs `DeclarativePackageSync`. Gmail, QuickBooks, and GitHub catalog rows keep their registered sync classes.

The sync installation must be bound to a Track before manual or scheduled sync.
Both use the same materialization runtime. Core owns leases, provenance
(`<install>:<external_id>`), conflict handling and cursor checkpoints. When
no trusted adapter is registered for the package id, Core pulls `source` and
materializes `map` through that same runtime. Core saves the cursor after
materialization.

### channel — inbound and outbound messaging

```yaml
capabilities:
  channel:
    mode: webhook
    ingress:
      verify: { scheme: hmac_sha256, header: X-Signature, secret: webhook_secret }
      event_id: "$.id"
      sender: "$.from"
      text: "$.message.text"
    send:
      request:
        method: POST
        path: /api/messages
        body: { to: "{{input.recipient}}", text: "{{input.text}}" }
```

Core hosts the webhook, verifies the signature, and maps the
sender to an identity only through an explicit verified link. When `ingress.timestamp`
is set, that unix time must fall within `max_skew_seconds` of now. The default
window is 300 seconds and the maximum is 900. A webhook without `timestamp`
keeps the signature and event-id checks. A linked message
queues one chat turn for the connector owner in the installation workspace,
using the same turn submission as other channels. The event is recorded after
that turn is accepted, so a failed submission can be retried. An unlinked
message is accepted and does not start a turn. `mode: managed-text-v1` uses a bundled first-party
adapter implementing the existing `ChannelPackage` lifecycle, for channels
such as Web Chat that need their own visitor transport. A trusted first-party
package registers that channel at startup. The Web Chat package does this. Its channel definition sets `visitor_surface`,
and Settings uses that flag for embed sharing, the pending-setup copy, the
private test, and the full-page share check. The private test and embed reader call the channel id from that definition.
The embed reply is kept only when it names the same id. Share, embed, and
private-test responses name that channel id. When the managed-channel registry is
configured, the private test, full-page share, embed-share, and public chat page accept
the id only if the channel sets `visitor_surface`.
A user-dropped folder is not imported. A serving host still replaces the
registry before it accepts visitor traffic. Settings no longer opens the older connection-profiles Web Chat card or its
configuration dialog, and those profile routes are no longer served. Web Chat
is configured through the managed channel. WhatsApp is host-managed:
its grants stay on `WhatsAppChannelBinding` and `USES_WHATSAPP_LINE`, and
generic Add refuses to create a second installation. The catalogue asks each host-managed package adapter for its own workspace binding. Core does not choose that binding by package id. The binding read also
returns the package grant status: the edge, whether a grant is present, and
whether that line is enabled. Ciphertext stays on the binding. The dropped-in
package panel shows that status, can save the jvconnect URL and API key through
the existing binding, and can disconnect the workspace line. Those controls
follow the package grant, not the package id. The connector library points a
host-managed package at Dropped-in packages. The connected list shows a
host-managed package that already has a grant, using that package's name and
enabled state. The grant stays on the existing binding. A package that is not
host-managed stays a normal catalog install. The API key is
not read back. Disconnect uses the existing binding and does not copy the
secret or delete the binding. Settings no longer opens the old WhatsApp
screen to configure that line.

## Trust tiers and admission

- Operator packages contain a declarative manifest and optional notes and
  icon files. Core rejects executable contents and schema fields that select code.
- Core ships first-party integrations as source. Fixed Core imports wire
  Gmail, QuickBooks, GitHub Issues, Web Chat and WhatsApp, and bundled
  definitions determine the permitted stdio commands. Developers who need a
  new native integration can fork Core and build it with their source changes.
- Rescan reports invalid or duplicate packages. An installation whose package
  disappears stays available for Disable and Remove. The stored fingerprint
  keeps an installed version stable until an explicit upgrade.

## Lifecycle in the app

| Step | Behavior |
|---|---|
| Rescan | Admin action in Settings → Connectors. Declarative changes apply without restart. A trusted package admitted at startup stays on that manifest until the process restarts; a new trusted package is not admitted by Rescan. Tools for a package that is no longer admitted are removed. |
| Library | Admitted packages whose binding includes the caller's scope. The workspace library omits a personal-only package. Rejected packages stay listed with their reason. |
| Add | Creates a disabled installation for the chosen scope and stores that manifest on the installation. Later rescans do not change it. Workspace cardinality defaults to one and refuses a second installation in that workspace. `many` allows another. The check and create for one package in one workspace are serialized in the process. |
| Configure | Form rendered from `configuration` by Core's shared settings renderer. Values are validated server-side; secrets are stored as encrypted handles and never read back. A blank or omitted secret keeps the stored handle. JSON `null` revokes that secret so a later call cannot use it. Settings sends `null` from Remove. Each save increments `package_revision` and disables the installation until the next health probe. |
| Enable | Requires a passing health check for the current revision. MCP uses `tools/list`. Other drop-in packages declare `health.request`. |
| Use | Every tool call, sync run and channel delivery rechecks the caller, installation, scope and revision. Writes stage review. A call, sync pull, or accepted webhook records an audit receipt naming the package, action, effect, and status. The receipt omits input, output, message text, and secrets. |
| Disable | Saves the installation as disabled before it clears a staged review. A second Disable leaves it off. A newer enable is left in place. |
| Remove | Disables the installation, clears its secret handles, rollback secrets, and staged input, then deletes the row. If the delete fails, the disabled row remains so Remove can be retried, and the cleared handles cannot be used. |
| Upgrade | Moves the installation onto the currently admitted manifest. Configuration and secret fields that still exist are kept. The previous manifest, configuration, and removed secrets are kept for one rollback. The installation is disabled until the next health probe. |

## Security rules

- Outbound declarative requests use Core's URL policy: loopback, private and
  link-local hosts are refused, DNS is pinned and redirects are re-validated.
  Requests with manifest-injected credential headers do not follow redirects.
  A declarative request follows a redirect only through that guarded client.
  OAuth token exchange and HTTP MCP also use guarded outbound transport.
  A `url` field with `egress: trusted_host` requires the operator's
  `INTEGRAL_OUTBOUND_TRUSTED_HOSTS` entry. That host is checked when the
  value is saved and again before each request.
- Secrets are injected only by `auth`, never templated, logged or returned.
- Manifests cannot grant themselves permissions; Core's workspace and policy
  checks apply to every call. A management call is refused when its workspace
  is not the installation's workspace.
- Writes and unlisted MCP tools always require review.
- Operator package files are data. Core never imports them.
- A connector's README, descriptions and tool output are untrusted content,
  never instructions.

## Validate and test

- `integral connectors validate <dir>` runs the scoped admission checks.
- `integral connectors new --kind actions|mcp|sync|channel <id>` scaffolds
  an operator declarative package.
- Qualification covers package lifecycle and review, workspace authorization,
  catalog migration and rollback, managed grant preservation, and rejection
  of executable files and unsupported manifests. The clean-wheel gate checks
  the schema and the bundled adapters.

Local qualification passed 544 focused backend tests, 102 Web Chat tests,
228 Settings tests, frontend type checking and the clean-wheel gate. A
Chrome smoke test in an isolated Mac installation confirmed Web Chat's
setup-pending state and disabled installs for the scaffold and first-party
sync and MCP connectors. The disposable loopback TLS host also served the
shipped Web Chat UI to isolated Chromium and Firefox. In both browsers a
synthetic visitor received a deterministic reply, resumed after reload, and
had no access to staff routes. A fresh browser profile had no access to that
visitor's history. Both browsers sent the exact origin and omitted the
referrer on visitor requests. The isolated browser
also completed health-gated Enable, Disable and Remove for declarative
actions, sync and MCP fixtures. Gmail and QuickBooks refused Enable before
OAuth authorization. WhatsApp grant preservation was tested with isolated
fixtures.

The full backend run on the development branch encountered 16 native-harness
failures. An isolated archive of `aa5144fe` reproduced the same 16 test IDs.
The scoped connector release uses its focused gates.

## Implementation phases

| Phase | Scope | Done when |
|---|---|---|
| 0 · Hygiene | Wire the managed-channel registry at boot or refuse to start; encrypt sync tokens with a reversible migration; rename the agent-dispatch registry; remove stale gating text and unwired UI. | No silent empty registry; no plaintext connector tokens. |
| 1 · Contract | Schema validation, folder loader and admission, installation record, one management API and Settings projection, migration preserving ids and grants. | The `actions` quickstart dropped into a root is admitted, added, configured, enabled, called, disabled and removed with no Core or frontend change. |
| 2 · Lanes | Declarative actions runtime with staged writes; manifest-fed MCP and sync lanes; OAuth setup action; shared renderer gains `secret`, `url` and OAuth. | MCP and sync examples pass the same test; Gmail, QuickBooks and GitHub load as packages; connector slug branches are gone from the API and Settings. |
| 3 · Channels | `channel` capability over the `ChannelPackage` lifecycle; Web Chat and WhatsApp as packages; legacy Web Chat path retired. | A webhook channel example passes; WhatsApp grants are preserved. |

Review each phase against [the substrate invariants](../INVARIANTS.md)
before changing its execution path.

- Hygiene preserves the rooted installation and policy edges
  (I-GRAPH-01, I-CON-04). Rewrite authentication state in place so rollback
  preserves identities, grants and cursors.
- The package lifecycle preserves workspace authority and scoped Settings
  caches (I-ROLE-01, I-GOVERN-01, I-UI-SCOPE-01). Prove rescan, management,
  read execution and review with a package outside Core.
- Lane migration preserves sync bindings and provenance (I-CON-01/02/04,
  I-SYNC-01) and checks the current installation and grants before effects.
  Use the existing MCP client and require review for unlisted tools
  (I-APPROVAL-01/02).
- Channel migration preserves installed grants and applies the current scope
  and exact provision checks at ingress and delivery (I-ACCESS-01).
- Release admission enforces the Core-owned execution boundary (I-CON-05,
  I-SUBSTRATE-01, I-EXT-01). Qualify the bundled adapters from the clean wheel
  and reject executable input from operator roots.

## Migrating existing connectors

Stop Core and take a database backup before rewriting legacy connector
credentials. Sync credential writes require `INTEGRAL_CREDENTIAL_ENC_KEY`.
With the deployment's database and credential key settings
exported, run `integral connectors migrate-credentials --dry-run`, then
`integral connectors migrate-credentials`. Only authentication state is
rewritten. Keep the encryption key with the backup. For a stopped deployment
rolling back to a version that requires plaintext credentials, the same script
supports `--reverse` with the original key.

With Core still stopped, run `integral connectors migrate-catalog --dry-run`
and then `integral connectors migrate-catalog`. The migration adopts admitted
sync and MCP catalog rows whose existing binding scope is supported.
It changes package metadata on the existing row and preserves credentials,
owner, Track edges, cursor and polling interval. A second run reports zero
changes. If a qualification check fails, use
`integral connectors migrate-catalog --reverse` before serving package
configuration changes. Reverse restores the captured management metadata.
Rows without an admitted package or workspace remain on the compatibility path.

Current catalog entries map directly onto the manifest; ids are preserved.

| Current `catalog/*.yaml` | Manifest |
|---|---|
| `slug` | `id` |
| `display_name`, `description`, `icon` | `name`, `description`, `icon` |
| `kind: mcp`, `transport`, `url`, `read_only_tools` | `capabilities.mcp` |
| `kind: sync`, `implementation.sync_connector_class` | `capabilities.sync` with a trusted adapter until a declarative `source` replaces it |
| `auth.type`, `auth.fields` | `auth` plus `configuration` fields (`secret: true` → `type: secret`) |
| `oauth.authorization_endpoint`, `token_endpoint`, `scopes`, `extra_authorize_params` | `auth: { type: oauth2 }` with the same values |
| `auth.type: none` on servers that run MCP authorization at connect time (Notion) | `auth: { type: mcp_oauth }` |
| `auth.type: env` (GitHub Issues) | `auth: { type: operator, env: <NAME> }` |
| `command`, `args`, uppercase env-var fields, `env_defaults` (stdio MCP) | `mcp.command` plus `mcp.env` typed references; trusted tier |
| `connection_mode` (per user / shared) | `binding.scopes` and `cardinality` |

All eleven current catalog entries were mapped through this table and validate
against the schema. When the package is admitted, Gmail and QuickBooks consent
and token URLs come from the manifest. Their client-id and redirect
environment names are `client.id_env` and `redirect_env` on those packages.
Google Drive, Gmail, and Sheets list the same Google client env names.
Workspace OAuth catalog installs use the package installation service when
the manifest supports workspace binding. In the package panel, Add saves a
disabled installation and Authorize opens the provider's consent screen.
The callback updates the same installation. Enable checks the provider using
the stored credentials.

Provider-registered callback paths continue to serve existing
installations. Catalog setup and
`POST /agentive/connector-packages/{id}/oauth/start` share the manifest
consent builder. Gmail, QuickBooks and GitHub Issues continue to use their
registered sync adapters, with request and mapping metadata supplied by their
manifests. Catalog-backed rows keep their Connector id, credentials, Track
bindings and cursors. WhatsApp exposes its existing workspace grant through the managed-channel
registry. The managed credentials endpoint saves a disabled grant. Enable
registers the line only when necessary and authenticated Disable and Remove
operate on the exact grant revision. Other workspaces keep their grants.
Queued replies remain bound to the grant that admitted the message. The
dedicated API remains available for older clients, and provider ingress
continues to verify the signed JV Connect webhook.
Web Chat's management adapter loads from its bundled
package during Core startup.

Keep the compatibility routes available for older clients during migration.
