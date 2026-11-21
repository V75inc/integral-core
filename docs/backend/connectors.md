# Connectors

Connectors give Integral declared paths to external information and capabilities. Native synchronization and external MCP mounts share lifecycle concerns but have different execution contracts.

## Catalogue and connection lifecycle

The Connector Library presents one collection of definitions. The Settings
catalogue projection gives each definition a stable ID, a provider, supported
scopes with cardinality, a list of capabilities (`api`, `mcp`, `channel`), and
the setup flow. Capabilities can be combined. Existing curated package IDs are
their API slugs. Their current install metadata and endpoints still come from
the server. Built-in adapters for Custom MCP and WhatsApp use their existing
specialized endpoints. Catalogue metadata describes the product surface.
Authorization and instance cardinality are enforced by the owning service.

**Add** opens setup for a new connection. A singleton displays **Configure**
once its connection exists. **Connected** lists actual connections, with
configuration and removal on each instance where the caller can manage it.
Custom MCP supports multiple connections, with one workspace connection per
server identity under the current normalized-name guard. Curated packages allow
multiple per-user connections and one workspace connection per package.
**Workspace** is the label for the API's `shared` scope. Eligible workspace
members can use that connected account. The connection owner and authorized
workspace admins and owners can manage it.

## WhatsApp (JV Connect)

**Settings → Connectors → Add Connector → WhatsApp (JV Connect)** opens an
in-place dialog for the workspace line and personal number settings.
Installed lines also open that dialog from their row in **Connected**. The catalogue ID is
`whatsapp_jvconnect`, and `/settings#whatsapp` still opens the same panel.
Connections keep their channel binding and identity records. A future direct
Meta integration can use a separate definition and its own credentials.

The workspace line is a singleton. Add and Configure use the existing binding
PUT with an explicit expected binding ID. A null ID requires an empty workspace.
A supplied ID must match the current grant, and a verified number cannot be
silently replaced from Configure. Omitting the field preserves the original
PUT behavior. Workspace grant changes serialize by workspace in PostgreSQL,
with an in-process lock used by the local JSON fixture. Disconnect removes the
workspace's grant. Other workspace grants and personal number links remain.

[Workspace Email](../platform/workspace-email.md) stays under **Settings →
Workspace → Email** for outbound delivery.

## Public Web Chat preview and delivery boundary

The explicitly enabled loopback preview now serves a full page and a same-origin
iframe demo. They render the same chat and share one disposable channel pinned
to the synthetic preview assistant. Both use the same session and message
handlers, per-visitor rate and history limits, and deterministic tool-free
replies. Only the iframe presentation accepts a same-origin parent. The main
page remains unframeable. The preview has no catalogue entry or share URL in
Settings.

The managed route already records one eligible published public profile and a
separate enabled flag. Product delivery still needs a trusted hosted adapter that
supplies a server-issued visitor identity and checks the published profile,
route generation, and enablement before every history read and turn. Hosted
and embed must use that same route and policy. Real embeds also need explicit
workspace-authorized origins and an origin-scoped frame policy. An inactive or
unassigned route must expose neither a link nor embed code.

## Native synchronization

A native connector implements the registered sync interface and binds to a Track through `IS_CONNECTED_TO`. It supplies stable external identity, record transformation, credential requirements, and deduplication semantics. Scope and policy gates precede synchronization.

Provenance keeps `source: connector` separate from the connector/external source identity. Audit actions carry the connector actor rather than substituting the human who requested a sync. Repeated records follow the configured update/conflict branch rather than creating duplicates.

Supported conflict modes include mirror-only, last-write-wins, and manual resolution. Conflict records are rooted participants with authorized reads and resolution tied to the underlying Entry. Do not reuse the obsolete assumption that every authenticated caller can list all conflicts.

## External MCP

An MCP connector mounts an external server's declared tools. Installation, refresh, reauthorization, and source configuration belong to its connector lifecycle. These tools do not establish a peer-agent delegation fabric.

Inbound MCP exposes Integral to external agents; outbound MCP exposes an external server to the resident. Keep credentials, transport origins, and authority distinct in each direction.

### Local stdio credential storage

Set `MCP_STDIO_TOKEN_STORE_ROOT` to a persistent directory writable by the API
user when Core source or installed packages are mounted read-only. The default
remains `backend/.data/mcp-stdio` for existing installations. QuickBooks MCP
writes its rotating refresh-token file here; the child process must see the same
path on every spawn. Files retain mode `0600`, workspace subdirectories and
random filenames; connector cleanup refuses paths outside the configured root.
Changing this root does not migrate previously issued token files. Retain the
old directory until affected connectors have been reauthorized and cleaned up.
Do not commit or log token-file contents.

Business's local source overlay sets `/data/mcp-stdio` on its existing persistent
`/data` volume and provides Node 22, npm/npx and Git for catalog stdio packages.
Rebuild/recreate the API after changing container configuration; OAuth consent
and a real provider tools/list still need qualification in the user's session.

### Cloudflare

The curated Cloudflare entry uses the official Streamable HTTP endpoint at
`https://mcp.cloudflare.com/mcp`. Add it from Settings → Connectors → Add
Connector, then complete Cloudflare sign-in and permission selection. Core
discovers OAuth metadata from the initial authentication challenge; the
catalog's `auth.type: none` means no credential field is required in Integral's
install form, not that the provider is unauthenticated. No tools are admitted
while consent remains pending.

All Cloudflare tools remain subject to Integral's propose/bless approval gate,
including the provider's broad `search` and `execute` surface. This entry
certifies no read-only tools. Choose narrow account permissions before granting
access; do not infer resource limits from a connector label. Installing this
entry does not change an existing custom MCP connection.

For automation, the provider also supports scoped bearer tokens. The existing
Custom MCP form can supply an Authorization header; keep the token out of URLs,
chat, and committed configuration. See the
[official Cloudflare MCP documentation](https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/).

Catalog tests qualify the local OAuth handoff and approval classification with
a mocked external handshake. Real account consent, tool discovery, deployment,
and recovery require separate provider qualification.

## Add and qualify one

Register the implementation through its reviewed module path, declare its package/schema dependencies, and use generic hooks where domain transformation is required. Do not hardcode a domain connector into Core services.

Test authentication failure, scope denial, repeat-sync identity, malformed records, conflicts, revocation, cancellation, and external failures. Qualify the actual provider and credentials; registry presence is not proof that every advertised service works in production.
