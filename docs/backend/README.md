# Backend reference

The backend supplies the graph substrate, resource authority, App lifecycle, and intelligence perimeter. Start with the [architecture](../product/ARCHITECTURE.md), then choose the relevant contract.

| Area | Guide |
|---|---|
| App packages and lifecycle | [Bundle architecture](app-bundles-v1.md), [authoring](app-bundle-authoring.md) |
| Models and library | [Authoring and library](operational-model-authoring-and-library.md), [packages](operational-model-packages.md) |
| Public capabilities | [Extension contract](../platform/extension-contract-v1.md), [API source inventory](API_REFERENCE.md) |
| Projections | [App Home](app-home.md), [pagination](pagination.md), [recorded graph context](recorded-graph-context.md) |
| Resident | [Chat](ai-chat.md), [skills](skill-format-standard.md), [workspace overlay](workspace-agent-profile.md), [scaffold recovery](scaffold-recovery.md) |
| Review and work | [Prompt queue](prompt-queue.md), [bounded-work contract](work-mandate-contract.md) |
| Commercial hosts | [Entitlements, quotas and host extensions](../platform/commercial-host-integration.md) |
| Collaboration | [Reaction selection](reaction-selection.md) |
| Integrations | [Connectors](connectors.md), [files](attachment-agent-contract.md), [speech](speech-input.md), [voice/embedding configuration](voice-and-embedding-models.md) |
| Search | [Declarative indexing and retrieval](semantic-indexing.md), [model discovery](operational-model-search-index.md) |
| Tool evidence | [Source observations](tool-source-observations.md) |
| Account timing | [Time zones and reminders](user-timezone.md) |
| Authority and writes | [Credentials](model-credentials-byok.md), [service layer](service-layer.md) |

The [architecture decision index](adr/README.md) explains preserved boundaries. ADRs describe decisions, not release acceptance. Dated results live in [qualification records](../ops/QUALIFICATION.md); operational configuration belongs in the [operations guides](../ops/DEPLOY.md).
