# Explore Integral

Integral connects knowledge, software structure, and AI action in a shared operational environment. These guides move from the experience to the architecture, then into building and operating the system.

## Understand the idea

Start with [Meet Integral](product/INTRODUCTION.md). Continue with the [white paper](product/WHITE_PAPER.md) for the complete conceptual guide to the substrate, its layers, application composition, and governed AI execution.

The [concept](product/CONCEPT.md) states the purpose. The [architecture](product/ARCHITECTURE.md) describes the implementation, the [requirements](product/PRD.md) define behavior to preserve, and the [roadmap](product/ROADMAP.md) identifies further development and qualification.

## Use it

Start your own workspace with the [local installation and CLI guide](ops/LOCAL_INSTALLATION.md). Then follow the [user guide](user-guide/README.md) for workspaces, Apps and Tracks, records, views, sharing, the resident AI, and approvals.

Use [App and Track export/import](platform/resource-portability.md) to transfer configuration or records and attachments between workspaces.

Understand dependencies and decision evidence with [workspace graph context](product/GRAPH_CONTEXT.md).

## Build on it

All App authors follow the [App settings UI contract](platform/app-settings-ui.md):
App-specific settings controls belong in the standard App settings dialog.

Use the [efficient agent-read pattern](platform/extension-contract-v1.md#efficient-agent-reads)
for focused App queries, bounded evidence, concise skills and realistic latency
qualification. Domain calculations belong in Apps; Core supplies the generic
governed execution and retrieval boundary.

Follow the [App quickstart](developer/quickstart.md), then use the [extension contract](platform/extension-contract-v1.md), [background jobs for Apps](platform/background-app-jobs.md), [Operational Model guide](operational-models/README.md), and [backend reference](backend/README.md). Use [declarative indexing and retrieval](backend/semantic-indexing.md) for searchable fields, agent capabilities and permission-aware passages. [Model discovery](backend/operational-model-search-index.md) covers the separate package catalog. The [generated capability map](generated/capability-map.md) derives from source and must be regenerated rather than edited by hand.

The [semantic search improvement contract](platform/knowledge-retrieval-v1.md)
strengthens embedding-based hybrid retrieval, hosted CPU/memory controls,
automatic index repair, safe model migrations and shared agent evidence. Its
delivery boundary and quality/resource/scale gates distinguish implemented hardening from future work,
including selective JV Spatial enrichment and optional OKF interchange.

## Operate it

Use the [CLI guide](ops/LOCAL_INSTALLATION.md) for local startup, logs, backups, restore, and upgrades. For hosted environments, use the [deployment runbook](ops/DEPLOY.md), [configuration guide](ops/CONFIGURATION.md), [security guide](ops/SECURITY.md), and [qualification record](ops/QUALIFICATION.md). Commercial distributions also use the [commercial host integration guide](platform/commercial-host-integration.md); payment-provider and plan settings remain in the host. Deployment, publication, CI, and user-flow acceptance are distinct checks.

## Maintain it

The [contributor guide](developer/CONTRIBUTING.md) explains repository discipline. [Invariants](INVARIANTS.md) preserve the substrate's access, graph, extension, and execution contracts.

The [access contract](product/ACCESS_CONTROL_CONTRACT.md) defines workspace/resource authority and declarative App permissions. [Resource visibility](product/RESOURCE_VISIBILITY.md) covers private defaults and legacy read compatibility. Use [scope migration guidance](ops/view-scope-migration.md) before changing existing resource identities.

Use the [current release qualification](ops/RELEASE_QUALIFICATION.md) and [hardening follow-up](product/HARDENING_FOLLOW_UP.md) for candidate results and remaining work. Retired reports remain in Git history; retained candidate-specific evidence is linked from its current topic guide and does not establish release acceptance. Maintain one current guide per topic and regenerate source-derived references after route or capability changes.

Runtime hosting: [local/hosted composition contract](platform/runtime-composition-v1.md) and [ADR-015](backend/adr/015-runtime-composition.md).
