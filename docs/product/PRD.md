# Product requirements

Integral must provide a shared operational foundation in which people and AI can understand, shape, and act on knowledge. These requirements describe behavior to preserve; release acceptance needs separate evidence.

| Requirement | Expected behavior | Evidence direction |
|---|---|---|
| Connected knowledge | Rooted typed records and explicit relationships | Graph integrity, relation and cascade tests |
| Conformable structure | Validated models, views, drafts, and migrations | Compile, impact, migration and browser checks |
| Workspace authority | Scope and resource access enforced server-side | Multi-principal denial and revocation |
| Useful collaboration | Intentional roles, comments, shares, invitations | Access matrix and user-flow readback |
| Governed AI | Current capabilities and policy constrain effects | Tool, staging, approval and receipt checks |
| Product continuity | Scoped threads and accurate final transcripts | Reload, binding, cancellation and replay |
| App portability | Packages use public facades and definitions | External reference and Core-only tests |
| Honest projections | Errors differ from empty results | Home/dashboard/query parity |
| Accountable usage | Source and uncertainty remain explicit | Physical observations and reconciliation |
| Operational reliability | Store-specific guarantees and recoverable files | Artifact, PostgreSQL, restore and interruption proof |

## Human experience

People should recognize resources by name, navigate authorized relationships, understand where they are working, and distinguish a proposal from an applied change. UI hiding cannot supply privacy. Generation should produce inspectable artifacts and respect design-only intent.

## Boundary requirements

Core stays domain-neutral. Commercial rules and domain semantics remain in Apps/hosts. External agents use MCP; there is no peer-agent delegation fabric. Skills do not grant access. Current permissions survive revocation, continuation, and approval execution.

## Open acceptance

Native durable chat, multi-worker journeys, provider/BYOK matrices, connectors, and public bounded-work admission need their own qualification. The public mandate path remains non-runnable. Do not convert roadmap intent into a release claim.

See [architecture](ARCHITECTURE.md), [invariants](../INVARIANTS.md), and [qualification](../ops/QUALIFICATION.md).
