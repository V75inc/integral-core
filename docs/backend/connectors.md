# Connectors

Connectors give Integral declared paths to external information and capabilities. Native synchronization and external MCP mounts share lifecycle concerns but have different execution contracts.

## Native synchronization

A native connector implements the registered sync interface and binds to a Track through `IS_CONNECTED_TO`. It supplies stable external identity, record transformation, credential requirements, and deduplication semantics. Scope and policy gates precede synchronization.

Provenance keeps `source: connector` separate from the connector/external source identity. Audit actions carry the connector actor rather than substituting the human who requested a sync. Repeated records follow the configured update/conflict branch rather than creating duplicates.

Supported conflict modes include mirror-only, last-write-wins, and manual resolution. Conflict records are rooted participants with authorized reads and resolution tied to the underlying Entry. Do not reuse the obsolete assumption that every authenticated caller can list all conflicts.

## External MCP

An MCP connector mounts an external server's declared tools. Installation, refresh, reauthorization, and source configuration belong to its connector lifecycle. These tools do not establish a peer-agent delegation fabric.

Inbound MCP exposes Integral to external agents; outbound MCP exposes an external server to the resident. Keep credentials, transport origins, and authority distinct in each direction.

## Add and qualify one

Register the implementation through its reviewed module path, declare its package/schema dependencies, and use generic hooks where domain transformation is required. Do not hardcode a domain connector into Core services.

Test authentication failure, scope denial, repeat-sync identity, malformed records, conflicts, revocation, cancellation, and external failures. Qualify the actual provider and credentials; registry presence is not proof that every advertised service works in production.
