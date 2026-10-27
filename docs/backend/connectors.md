# Connectors

Connectors give Integral declared paths to external information and capabilities. Native synchronization and external MCP mounts share lifecycle concerns but have different execution contracts.

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
