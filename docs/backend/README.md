# Backend reference

The backend supplies the graph substrate, resource authority, App lifecycle, and intelligence perimeter. Start with the [architecture](../product/ARCHITECTURE.md), then choose the relevant contract.

| Area | Guide |
|---|---|
| App packages and lifecycle | [Bundle architecture](app-bundles-v1.md), [authoring](app-bundle-authoring.md) |
| Models and library | [Authoring and library](operational-model-authoring-and-library.md), [packages](operational-model-packages.md) |
| Public capabilities | [Extension contract](../platform/extension-contract-v1.md), [API source inventory](API_REFERENCE.md) |
| Projections | [App Home](app-home.md), [pagination](pagination.md) |
| Resident | [Chat](ai-chat.md), [skills](skill-format-standard.md), [workspace overlay](workspace-agent-profile.md), [scaffold recovery](scaffold-recovery.md) |
| Review and work | [Prompt queue](prompt-queue.md), [bounded-work contract](work-mandate-contract.md) |
| Integrations | [Connectors](connectors.md), [files](attachment-agent-contract.md), [speech](speech-input.md) |
| Authority and writes | [Credentials](model-credentials-byok.md), [service layer](service-layer.md) |

Architecture decisions in `adr/` explain preserved boundaries. They describe a current decision, not a frozen release acceptance claim. Operational configuration belongs in the [operations guides](../ops/DEPLOY.md).
