# Core acceptance ledger

**Purpose:** the single release-evidence record for Integral Core.

**Status:** preparation in progress; this is **not** a release declaration.
**Candidate:** not frozen. The next candidate must name the immutable Git
revision and the hashes of the Core wheel, SDK wheel, and independent App
archive before any mandatory gate is recorded as passed.
**Supported topology for qualification:** Core API and web bundle with
Postgres. SQLite and JSON stores support local development and reconciliation;
they do not establish multi-worker command, lease, or recovery guarantees.

This ledger supersedes the claim-oriented tables in
[RELEASE_CANDIDATE.md](RELEASE_CANDIDATE.md). The test-to-criterion mapping
remains in [ACCEPTANCE_TEST_MAP.md](ACCEPTANCE_TEST_MAP.md). A test can be
useful development evidence without qualifying the frozen candidate.

## How to record a candidate

1. Create a commit with the intended source and documentation state.
2. Record its SHA and build hashes below. Never substitute a branch name.
3. Run every mandatory command against that source/artifact combination.
4. Record the command, environment, timestamp, result, and retained artifact
   or trace for every row. `Skipped`, `not run`, and `blocked` remain visible.
5. Run the human/browser journeys against the same deployment topology.
6. Review the completed ledger. A release decision is separate from this
   evidence and requires explicit authorization.

## Candidate identity and environment

| Field | Required value for a qualified candidate | Current record |
| --- | --- | --- |
| Git revision | Full immutable SHA | `9269ad1783bf83acff90fe1accf3ae19ae961d53` automated rows only; not a release |
| Core wheel | Filename + SHA-256 | Not built for candidate |
| SDK wheel | Filename + SHA-256 | Not built for candidate |
| Independent App archive | Filename + SHA-256 + signature key identity | Not built for candidate |
| Container images | Image digests for API and web | Not built for candidate |
| Python, Node, Docker, Postgres | Exact versions | Not captured for candidate |
| Configuration | Non-secret settings digest; model/provider state | Not captured for candidate |
| Fixture / backup identity | Seed or backup digest and dataset version | Not captured for candidate |

## Mandatory gates

C6 fills this table once, for one frozen SHA. A green run on another revision stays outside the table. Skipped is not a pass. The external live-model exam is not a row here.

| Gate | Command or journey | Owner | Candidate result | Evidence to retain |
| --- | --- | --- | --- | --- |
| Repository gate | `make verify` | Release | Pass on `9269ad1` (2026-09-23) | Local command log |
| CI-faithful smoke | `make verify-ci` | Release | Pass, included in that `make verify` | Local command log |
| Core-only boundary | `make verify-core-only` | Platform | Pass on `9269ad1` | Local command log |
| Contract lane | `make verify-contract` | Extension | Pass on `9269ad1` | Local command log |
| Postgres proof | Applicable Postgres/contract suites against a fresh database | Persistence | Not run here. Local Postgres is the developer database. CI `test-postgres` is the lane. | CI run URL when that SHA is checked |
| Built Core | `make verify-artifact` and `make verify-clean-install` | Release | Pass, via `make verify` and `make verify-independent-artifacts` | Local command log |
| Built SDK | `make verify-sdk-artifact` | SDK | Pass on `9269ad1` | Local command log |
| Independent App | `make verify-external-asset-register` | Extension | Pass on `9269ad1` | Local command log |
| Browser acceptance | Ordinary signed-in journeys on the candidate deployment | Experience | Not a pass for `9269ad1`. A clean-database smoke of the already-running compose images (built before this SHA) passed on 2026-09-23. | [2026-09-23 clean-db browser smoke](evidence/2026-09-23-clean-db-browser-smoke.md) |
| Transport parity | UI, extension HTTP, resident, and MCP operation/query journeys | Execution | Partial. Contract tests cover extracted HTTP, resident, and MCP dispatch. UI on this SHA was not smoked. | Contract tests in `test_asset_register_artifact.py` |
| Restore drill | Fresh deployment restore of a populated fixture | Persistence | Pass on the local developer database, 2026-09-23. Counts and identity matched. Not a separate fixture database. | Drill log lines `counts match` and `identity match` |
| Human review | Architecture and release review | Product owner | Pending | Decision record |

## Finish-line acceptance matrix

| ID | Required outcome | Responsible area | Candidate status | Evidence requirement |
| --- | --- | --- | --- | --- |
| A01 | Core installs cleanly and boots without commercial Apps | Release / platform | Unproven | Clean install, first-login, API, and generic-view trace |
| A02 | Ordinary Core use remains available with the model provider unavailable | Platform / experience | Unproven | Browser and API proof with provider disabled |
| A03 | Module boundary violations fail the build | Platform | Unproven | Guard result and reviewed allowlist |
| A04 | Bad scope and revoked access fail at every effect boundary | Identity / execution | Unproven | UI, HTTP, resident, MCP negative tests |
| A05 | Fields stay stable across rename, nulls, and platform/business collisions | Information | Partial evidence only | Shared form, view, dashboard, and agent-query fixture |
| A06 | Concurrent command plus crash creates one effect and no duplicate receipt | Execution / persistence | Partial evidence only | Postgres concurrency and injected-crash trace |
| A07 | Approved revision executes once; correction, expiry, and cancellation report accurately | Execution / resident | Partial evidence only | Receipt and continuation/recovery tests |
| A08 | Build resumes after restart without duplicate objects | Applications / execution | Partial evidence only | Restart and requirement-ledger proof |
| A09 | Exact query and every rendered view agree above page limits and date boundaries | Query / experience | Verified | [2026-09-21 query and rendered-view parity evidence](evidence/2026-09-21-a09-query-view-parity.md) |
| A10 | Populated schema alteration preserves bindings or fails before unsafe change | Information / applications | Partial evidence only | Migration fixture and rollback/rejection trace |
| A11 | External unknown outcomes reconcile before retry | Execution | Unproven | Provider correlation and retry trace |
| A12 | Independent App has identical enforcement across UI, HTTP, resident, MCP | Extension / execution | Partial evidence only | Four-surface operation and query receipts |
| A13 | Upgrade preserves customization; pause/uninstall fence capabilities and work | Applications / extension | Partial evidence only | Populated upgrade, pause, restart, and uninstall drill |
| A14 | Restore reproduces records, edges, attachments, package identity, and work | Persistence / release | Partial evidence only | Restore inspection against the fixture digest |
| A15 | Active documentation is coherent, linked, and executable | Documentation / all owners | Partial evidence only | Link checks and independent trials |
| A16 | External live-model exam meets its own budgets | Intelligence / release | Unproven; does not block the platform | Versioned model configuration and retained evaluation traces kept outside Core |

## Public extension acceptance map

The historic Foundation sprint AC-01 through AC-14 are mapped to concrete
tests in [ACCEPTANCE_TEST_MAP.md](ACCEPTANCE_TEST_MAP.md). They are supporting
evidence for A01, A04, A06, A12, A13, A14, and A15 above. They do not replace
candidate-specific artifact, browser, restart, and recovery qualification.

AC-14, the sprint's publish digest gate, is implemented and recorded in
[the WP-10 closure](evidence/2026-09-22-wp10-sprint-closure.md). That closes
the public-developer sprint package. It does not mark the mandatory gates
above as passed, and it does not publish a release.

## Known limitations carried into the next candidate

- One durable transaction/effect-receipt authority does not yet cover every
  UI, HTTP, resident, MCP, and extension operation.
- Query, form, saved-view, and dashboard semantics are not yet proven to use
  the same field and projection resolver above page limits.
- Schema publication, backfill, record updates, and App lifecycle evolution
  still require their durable-plan and recovery proof.
- The independent App contract is covered by the extracted-archive journey
  and the restore drill. A frozen candidate still has to record those
  commands in this ledger.
- The external live-model exam and human acceptance are pending. A model miss does not change Core.

See [CORE_FINISH_STATUS.md](CORE_FINISH_STATUS.md) for the ordered build
program and [foundation-reset/implementation-plan.md](../foundation-reset/implementation-plan.md)
for work-package exits.
