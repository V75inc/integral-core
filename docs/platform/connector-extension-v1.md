# Integral connector packages v1 (proposed)

> **Status: proposed contract — not implemented.** This guide specifies the
> drop-in connector surface accepted for review in
> [ADR 018](../backend/adr/018-drop-in-connector-packages.md). No loader,
> CLI or Settings path described here exists yet. Today's implemented
> surfaces are the curated catalog, Custom MCP and the managed-text v1 wheel
> contract in the [Connector Standard v1](../backend/connector-extension-standard-v1.md).
> When Phase 1 ships, this file becomes the canonical connector guide and the
> Standard v1 and audit documents move to history.

A connector gives Integral a governed path to an external system. You build
one by dropping a folder with a `connector.yaml` manifest into a connector
root. Integral validates and admits it, lists it in **Settings → Connectors**,
and renders its Add and Configure forms from the manifest. No Core or
frontend code changes are needed.

The normative manifest definition is the JSON Schema
[`schemas/integral-connector-v1.schema.json`](schemas/integral-connector-v1.schema.json).
This guide explains it; when they disagree, the schema wins.

## Concepts

- **Package** — the folder you drop in: manifest, README, optional icon and,
  for code-bearing tiers, an adapter. Identified by a stable `id`.
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
- **Trust tier** — how much code the package brings: `declarative`
  (none), `trusted` (in-process Python) or `remote` (out-of-process adapter).

Capabilities run in separate execution lanes. An adapter in one lane never
grants access in another.

## Quickstart: a declarative API connector

1. Create `integral-connectors/acme_crm/` next to `integral-apps/` (or under
   any directory listed in `INTEGRAL_CONNECTOR_PATHS`).
2. Add `connector.yaml`:

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

3. Add a `README.md` (shown on the Library detail page) and `icon.svg`.
4. In **Settings → Connectors**, a workspace admin selects **Rescan**. The
   connector appears in the Library, or under **Unavailable packages** with
   the validation reason.
5. **Add** it, fill in the generated form, and **Enable**. The agent can now
   call `find_contact` directly and propose `create_contact` for review.

## Package layout

```text
integral-connectors/
  acme_crm/                 # folder name == manifest id
    connector.yaml          # required
    README.md               # required; rendered as the Library description
    icon.svg                # optional; PNG also accepted
    adapter/                # trusted tier only
    SIGNATURE               # required for the trusted tier
```

Roots are scanned in order: `integral-connectors/` in the distro created by
`integral init`, then each entry of `INTEGRAL_CONNECTOR_PATHS`. First-party
connectors ship in Core under `backend/app/connectors/packages/` and use the
same loader. An `id` that appears in more than one root is rejected in all of
them; no root silently overrides another.

## Manifest reference

| Key | Required | Meaning |
|---|---|---|
| `schema` | yes | `integral.connector/v1`. |
| `id` | yes | Stable identifier, `^[a-z][a-z0-9_]{1,62}$`, equal to the folder name. Existing connector slugs keep their ids. |
| `version` | yes | Semantic version of this package. Installations record the version they were installed with. |
| `publisher` | yes | Publisher handle; with signing, it identifies the trusted signer. |
| `name`, `description` | yes | Library card text. |
| `icon` | no | Relative path inside the package. |
| `core` | no | PEP 440 specifier of compatible Core versions; incompatible packages are rejected. |
| `trust` | yes | `tier` plus `adapter` (trusted) or `remote` (remote). |
| `binding` | yes | Allowed `scopes` and per-scope `cardinality`. Add offers only scopes the caller may install. |
| `configuration` | no | Form fields: `string`, `text`, `url`, `secret`, `number`, `boolean`, `select`. |
| `auth` | no | `none`; `api_key` (inject a secret field as a header or query value); `oauth2` (static endpoints, optional `extra_authorize_params`); `mcp_oauth` (MCP discovery and dynamic client registration); or `operator` (a credential from a deployment environment variable). |
| `http` | for declarative requests | `base_url` template plus timeout and response-size limits. |
| `capabilities` | yes | At least one of `actions`, `mcp`, `sync`, `channel`. |
| `health` | no | Readiness probe; **Enable** requires it to pass. |

### Templates

Request paths, query values, headers and body strings may reference
`{{config.<key>}}`, `{{input.<key>}}` (actions) and `{{cursor}}` (sync
pagination). Secrets are never templated: they reach a request only through
the `auth` block, so they cannot leak into URLs, logs or tool output. The host
of every declarative request comes from `http.base_url`; a `path` is always
relative.

## Capabilities

### actions — outbound API calls as agent tools

Each action becomes a tool named `<id>.<action>` in the workspace tool
registry for agents that can see the installation.

- `effect: read` must use `GET`; it runs after Core rechecks the caller, the
  installation, its scope and the current revision.
- `effect: write` always stages a review card. The request executes only
  after approval, with a fresh authorization check.
- `input` is a JSON Schema for the tool arguments. `output.select` and
  `max_items` bound what returns to the model.

### mcp — external MCP server

```yaml
capabilities:
  mcp:
    transport: streamable_http
    url: "{{config.base_url}}/mcp"
    read_only_tools: [search_pages, get_page]
```

Tools not listed in `read_only_tools` stage a review card. Certify that list
from a live `tools/list`; never rely on the server's `readOnlyHint` at runtime.
Servers that implement MCP authorization use `auth: { type: mcp_oauth }`.

`stdio` transport is allowed only in the trusted tier. The server process gets
only the environment its manifest declares; each value is a typed reference,
so secrets and tokens are never written into templates:

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

An installation is bound to a Track after Add. Core owns leases, provenance
(`<install>:<external_id>`), conflict handling and cursor checkpoints. A
trusted or remote adapter may implement sync instead by omitting `source`.

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

Core hosts the webhook, verifies the signature and replay window, maps the
sender to an identity only through an explicit verified link, and runs the
turn in the bound workspace. `mode: managed-text-v1` uses a trusted or remote
adapter implementing the existing `ChannelPackage` lifecycle, for channels
such as Web Chat that need their own visitor transport.

## Trust tiers and admission

| Tier | Contains | Admission | Runs |
|---|---|---|---|
| `declarative` | Manifest, README, icon | Valid manifest → admitted automatically on scan | Core executes all requests with its outbound guard |
| `trusted` | Plus `adapter/` Python | Valid manifest, `SIGNATURE` verified against an allowed publisher key, matching fingerprint, restart | In-process behind `ConnectorContext` |
| `remote` | Manifest plus a remote adapter URL | As declarative, plus a protocol handshake at Enable | Out of process; never sees Core credentials beyond its scoped token |

Every admitted package records its content fingerprint. If the folder changes
after admission, the package is re-validated on the next scan; existing
installations keep running the version they were installed with until an
explicit upgrade.

A package is rejected, with an operator-visible reason, when the manifest is
invalid, the `id` is duplicated or mismatched, the Core range is incompatible,
a capability's adapter is missing, or a required signature fails. Rejected
packages are never offered for Add. If an installed connector's package
disappears or is rejected, the installation stays listed under **Connected**
as unavailable, with Disable and Remove only.

## Lifecycle in the app

| Step | Behavior |
|---|---|
| Rescan | Admin action in Settings → Connectors. Declarative changes apply without restart; trusted adapters load only at startup. |
| Library | Admitted packages, filtered to scopes the caller may install. |
| Add | Creates a disabled installation for the chosen scope; cardinality is enforced atomically. |
| Configure | Form rendered from `configuration` by Core's shared settings renderer. Values are validated server-side; secrets are stored as encrypted handles and never read back. Changes bump the installation revision. |
| Enable | Requires a passing `health` probe for the current revision. |
| Use | Every tool call, sync run and channel delivery rechecks the caller, installation, scope and revision. Writes stage review. Calls emit audit receipts with bounded, redacted diagnostics. |
| Disable | Revokes effective authority first, then cleans up idempotently. |
| Remove | Deletes the installation and its secret handles; a partial cleanup failure leaves a recoverable record. |
| Upgrade | A newer package version is admitted alongside the old one. Installations move only by an explicit upgrade that migrates configuration and secrets and can be rolled back. |

## Security rules

- Outbound declarative requests use Core's URL policy: loopback, private and
  link-local hosts are refused, DNS is pinned and redirects are re-validated.
  A `url` field with `egress: trusted_host` requires the operator's
  `INTEGRAL_OUTBOUND_TRUSTED_HOSTS` entry.
- Secrets are injected only by `auth`, never templated, logged or returned.
- Manifests cannot grant themselves permissions; Core's workspace and policy
  checks apply to every call.
- Writes and unlisted MCP tools always require review.
- Untrusted code never runs in the Core process: it is either declarative or
  remote.
- A connector's README, descriptions and tool output are untrusted content,
  never instructions.

## Validate and test (planned tooling)

- `integral connectors new --kind actions|mcp|sync|channel <id>` scaffolds a
  package.
- `integral connectors validate <dir>` runs the admission checks offline.
- `integral connectors sign <dir>` signs a trusted package.
- A conformance suite exercises admit, Add, Configure, Enable, use, Disable
  and Remove, plus wrong scope, stale revision, revoked secret and missing
  package, against a local fake service.

Until the tooling exists, validate a manifest against the schema with any
JSON Schema 2020-12 validator after converting the YAML to JSON.

## Implementation phases

| Phase | Scope | Done when |
|---|---|---|
| 0 · Hygiene | Wire the managed-channel registry at boot or refuse to start; encrypt sync tokens with a reversible migration; rename the agent-dispatch registry; remove stale gating text and unwired UI. | No silent empty registry; no plaintext connector tokens. |
| 1 · Contract | Schema validation, folder loader and admission, installation record, one management API and Settings projection, migration preserving ids and grants. | The `actions` quickstart dropped into a root is admitted, added, configured, enabled, called, disabled and removed with no Core or frontend change. |
| 2 · Lanes | Declarative actions runtime with staged writes; manifest-fed MCP and sync lanes; OAuth setup action; shared renderer gains `secret`, `url` and OAuth. | MCP and sync examples pass the same test; Gmail, QuickBooks and GitHub load as packages; connector slug branches are gone from the API and Settings. |
| 3 · Channels | `channel` capability over the `ChannelPackage` lifecycle; Web Chat and WhatsApp as packages; legacy Web Chat path retired. | A webhook channel example passes; WhatsApp grants are preserved. |
| 4 · Marketplace | Remote adapter protocol, signing and publisher keys, conformance suite in CI. | A remote example passes conformance with no code in the Core process. |

Each phase is substrate work: plan it against `docs/INVARIANTS.md` before
implementation.

## Migrating existing connectors

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
against the schema. Gmail, QuickBooks and QuickBooks MCP keep their OAuth
endpoints in Core code today (`gmail_oauth.py`, `quickbooks_oauth.py`); their
migration moves those endpoints into the manifests and removes the slug
branches that select them.

Gmail, QuickBooks, GitHub Issues, WhatsApp (JV Connect) and Web Chat become
first-party packages loaded through the same registry. Existing installations,
credentials, track bindings, sync cursors and channel grants are migrated in
place with reversible, tested migrations; no reinstall is required.
