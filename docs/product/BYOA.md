# Bring your own agent

External agents connect to Integral through MCP. They can discover and invoke exposed capabilities under authenticated identity, workspace scope, resource permissions, and applicable policy/review paths.

The external agent remains responsible for its own reasoning and model environment. Integral remains responsible for the substrate perimeter and resulting authorized effects. A tool description or skill cannot replace a grant.

Use the deployed MCP transport and authentication configuration. Inspect current tool discovery rather than hardcoding an old catalog. Supply required scoped inputs and preserve the backend's error and result semantics.

Reads and writes need separate qualification. Test two principals/workspaces, private library visibility, revocation, invalid scope, proposed changes, approval application, and exact record readback. An HTTP success must not be presented as a completed effect if the result reports review or uncertain status.

Inbound MCP is distinct from outbound external MCP connectors. Neither establishes an agent-to-agent peer delegation fabric. For architecture, read [resident harness](RESIDENT_HARNESS.md); for application capabilities, use the [extension contract](../platform/extension-contract-v1.md).
