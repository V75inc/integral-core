# Backend architecture decisions

These short records preserve decisions and boundaries. Their status statements
are not release evidence; use the [qualification guide](../../ops/QUALIFICATION.md)
for dated checks against a specific source or artifact.

| ADR | Decision | Status |
|---|---|---|
| [001](001-model-credentials-byok.md) | Server-side model credentials | Accepted boundary |
| [002](002-contributed-skill-safeguard.md) | Contributed skill trust | Partially implemented; marketplace design remains open |
| [003](003-singular-resident-harness.md) | One resident per active binding | Accepted architecture |
| [004](004-app-composition-domain-apps-capability-bundles-scheduled-skills.md) | Domain Apps and reusable capabilities | Proposed direction; not a completed suite reorganization |
| [005](005-single-worker-until-shared-turn-state.md) | Conservative worker posture | Single-worker remains the supported baseline |
| [006](006-personal-context-stream-unstaged.md) | Bounded unstaged observations | Accepted narrow boundary |
| [007](007-decision-ledger-from-staging-terminals.md) | Preserve review outcomes | Accepted boundary |
| [008](008-toolcontext-own-workspace-writes.md) | Caller-owned personal workspace writes | Accepted scoped facade boundary |
| [009](009-mcp-as-connector.md) | External MCP servers as connectors | Accepted direction |
| [010](010-connector-subsystem-architecture.md) | Connector subsystem boundaries | Current architecture boundary |
| [011](011-public-app-extension-platform.md) | Public App capabilities | Current extension boundary |
| [012](012-intrinsic-agentive-queryability.md) | Queryable App information | Current query contract |
| [013](013-operational-model-vocabulary.md) | Operational Model vocabulary | Current public terminology |
| [014](014-app-background-jobs.md) | Background jobs for Apps | Proposed boundary |

Update the relevant ADR when a durable boundary changes. Routine implementation
and qualification results belong in Git and the dated qualification records.
