# Documentation replacement program

**Scope:** The entire authored repository documentation surface, not only `docs/product/ARCHITECTURE.md`.
**Status:** Active authority is `AGENTS.md` and `docs/product/CORE_FINISH_STATUS.md`. This plan is the migration map, not a second product. D4 runs only as part of foundation-reset WP-09, after C6 freezes one SHA. No tracked `CLAUDE.md` remains in the working tree.

## Policy

Replace the active corpus from the new contract outward. Do not patch historical phase terminology indefinitely or copy speculative claims into the new reference. Git preserves history; an enormous browsable legacy tree must not remain alongside active guidance by default.

Retain legal/license/attribution material and review it for accuracy; architectural reset does not authorize discarding legal obligations. Preserve incident/review evidence needed for acceptance until it is incorporated into tests and the release ledger. Prior ADRs are decision history: mark superseded and link successors, then retain a compact historical register rather than presenting obsolete decisions as current rules.

`AGENTS.md` is the sole agent-guidance authority. During migration, the current `CLAUDE.md` files remain in force only because the existing root `AGENTS.md` delegates to them. At cutover, move all applicable guidance into the nearest `AGENTS.md`, update nested guidance the same way, remove every `CLAUDE.md`, and remove every delegation or link to one. A target design must never silently disable existing substrate rules.

## New active information architecture

| Path | Audience and authoritative content | Accountable owner |
|---|---|---|
| `README.md` | Product promise, supported capability envelope, quickest verified install, license and entry links | Product/release |
| `CONTRIBUTING.md` | Reproducible development, code review, verification and contribution boundaries | DX |
| `AGENTS.md` | Sole concise shared agent authority, with scoped nested guides where needed; no duplicated architecture essay | Architecture/DX |
| `docs/README.md` | Role-based navigation and document status conventions | Documentation |
| `docs/product/overview.md` | Intelligent operational environment, audience, goals, non-goals and glossary | Product |
| `docs/product/capability-envelope.md` | Supported declarative behavior, extension needs, honest unsupported cases | Product/applications |
| `docs/architecture/overview.md` | Context/container/module diagrams, dependency rules and deployment | Architecture |
| `docs/architecture/modules/*.md` | Ownership, public interfaces, persistence and prohibited dependencies per module | Module owners |
| `docs/architecture/invariants.md` | Current invariants with stable IDs, rationale, enforcement and tests | Architecture |
| `docs/architecture/decisions/*.md` | New ADRs, accepted/rejected alternatives and supersession links | Decision owners |
| `docs/contracts/*.md` | Scope/policy, information, definitions, commands, queries, work/approvals, evidence, extensions | Contract owners |
| `docs/guides/operator/*.md` | Install, configure, backup/restore, upgrade, recover, diagnose and release trust | Operations |
| `docs/guides/user/*.md` | Design/build, operate/query, collaborate and evolve an App | Experience |
| `docs/guides/developer/*.md` | Core setup, extension tutorial, SDK, custom views, migrations and testing | DX |
| `docs/reference/` | Generated API/SDK/configuration/capability references and exact compatibility matrix | Contract/release |
| `docs/quality/` | Test strategy, scenario fixtures, acceptance ledger, supported models and performance budgets | Quality |
| `docs/roadmap/` | One active roadmap and bounded implementation packages | Product/engineering |
| `docs/history/README.md` | Compact retired-decision/provenance index with Git revision links | Documentation |
| `agent/.../skills/` | Executable resident instructions aligned to capability contracts; shipped product resources | Intelligence |
| `examples/*/README.md` | Independently executable public-contract examples | DX |

The reset planning package remains active until the program closes, then its decisions move to the accepted ADR register and its execution history leaves the active roadmap. Do not retain both old and new roadmaps as current.

## Disposition rules

The companion CSV assigns each baseline Markdown path: category, action, target, owner and closure condition. It is a proposed migration ledger, not evidence of detailed content review of every file.

- **REWRITE:** extract verified requirements; author a replacement; test its instructions; fix inbound links; remove old file or leave a minimal redirect only where necessary.
- **SUPERSEDE:** map relevant decisions to successor ADRs and tests; mark retired; remove obsolete active navigation. Preserve historical provenance in Git/index.
- **REAUTHOR_RUNTIME:** rewrite skills/prompts only after executable contracts exist; test packaging, discovery and complete journeys. Do not delete a shipped resource as though it were a prose note.
- **RETAIN_REVIEW:** preserve legal/governance/history files where appropriate; remove obsolete claims without losing required notices or attribution.
- **EVIDENCE_TO_TESTS:** preserve observed failures until regressions and acceptance assertions exist; retain compact evidence references, not competing product instructions.

Bulk category assignment is triage. WP-00 must review each row before destructive action and enumerate non-Markdown guidance, embedded prompts and rendered docs. A ledger row is closed only when its replacement/removal, links, executable claims and relevant acceptance evidence have been checked.

## Work sequence

### D0 — Inventory and authority map (WP-00)

Enumerate tracked and local-authored docs, hidden `.planning`/`.claude`/`.cursor`/`.kiro` guidance, nested instruction files, runtime YAML prompts, example configuration, CLI help and CI references. Exclude dependencies and generated/vendor content explicitly. Map every source-of-truth claim and every invariant to an owner.

Record legal provenance, inbound links, code links, generated status, shipped-resource status, disposition and verification command. Pin inventory to the source revision. Add new files to the ledger during implementation so coverage cannot drift.

### D1 — New conceptual and architectural foundation (WP-01/02)

Write product overview, glossary, module ownership and dependency diagrams. Separate existing behavior, target decisions and qualification status. Resolve overloaded terms: App/package/instance/profile, record/entry, field/key/label, operation/attempt/effect, approval/authorization, applied/verified and policy/role.

Replace the chronological invariant accumulation with a current thematic register. Preserve existing invariant IDs or publish an explicit old-to-new mapping. Each invariant names code enforcement and test evidence; obsolete ones have retirement rationale. No rule vanishes merely because its old phase is retired.

### D2 — Contracts and executable examples (WP-02–05)

Write contract references from actual schemas and public APIs. Generate repetitive field/signature/configuration tables. Hand-author semantics, guarantees, failure behavior and examples. Document nullability, scope, pagination, concurrency, time zones, authorization, idempotency and recovery—not just happy-path payloads.

Tutorial commands run in clean environments against built artifacts. Keep development-from-source and operator installation distinct. Use one canonical example per concept and link to it; avoid copied setup sequences.

### D3 — Agent, developer and operator instructions (WP-06–08)

Replace resident skill content, remove duplicated workflows and align tool descriptions with generated capabilities. Rewrite contributor and coding-agent guides to enforce module seams and current jvspatial constraints. Update nested guides at the same time; leaving stale local overrides is a release failure.

Write complete public extension and operation tutorials, lifecycle/migration recipes, deployment, recovery, backup/restore and troubleshooting guides. Instructions identify supported topology and external-provider limits. Remove commercial/private repository prerequisites from Core paths. Consolidate all current `CLAUDE.md` content into their authoritative `AGENTS.md` replacements and update every agent/tool discovery reference.

### D4 — Cutover and removal (WP-09)

Replace docs navigation, root README, product roadmap, acceptance map and agent entry points in one coherent cutover. Remove superseded active pages and old planning directives after closing their rows. Remove every `CLAUDE.md` only after its content is consolidated into `AGENTS.md`, all inbound references are gone, and the applicable agent/tool discovery checks pass. Historical evidence remains discoverable through a concise index and Git references; runtime discovery must not ingest it as current instruction.

Run link/anchor checks, case-sensitive path checks, orphan-page and duplicate-authority checks, generated-reference drift checks, forbidden legacy terminology scans with reviewed exceptions, and documented-command smoke tests. Perform independent docs-only operator and extension-author trials.

## Documentation quality gates

- Every active page has an owner, purpose, status and related implementation/contract version where relevant.
- Every guarantee maps to an acceptance ID and evidence; planned functionality is explicitly labeled.
- Every active local link and anchor resolves; every retired inbound link is deliberately redirected or updated.
- Generated references match the candidate artifact. Documentation is built/tested from the same revision as the code.
- No active guide depends on the commercial tree, private imports, unpublished local harness paths or tribal setup knowledge.
- Legal notices and existing supported security constraints remain intact.
- No duplicate active authority for architecture, invariants, setup or roadmap.
- Every inventory row has a verified disposition. Non-Markdown product instructions have equivalent coverage.

## Definition of documentation completion

An unfamiliar operator can install, configure, recover and upgrade Core using only the supported public material. An unfamiliar developer can build and install the reference extension without private assistance. A coding agent can identify the correct module, contracts, invariants and verification gates without interpreting obsolete phase plans. Documentation describes the qualified product rather than the project's accumulated aspirations.
