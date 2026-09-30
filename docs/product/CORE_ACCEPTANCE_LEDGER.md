# Core acceptance ledger

**Purpose:** the single release-evidence record for Integral Core.

**Status:** preparation in progress; this is **not** a release declaration.
**Candidate:** not frozen. The latest broad integration checkpoint is
`60f6e6fa4c22181ba17bba7b7e2d6001678cafc8`, parented on
`bbd55db6335e5956c643debcc9c04179e9699612` with local Ollama support; the
candidate requalification is recorded [here](evidence/2026-09-29-integrated-candidate-60f6e6f-requalification.md).
Supplemental combined code candidate `746400a690a18856b2e00b1c8dd0242cefefccab`
integrates W5.2, W6.2, and PR #93 dependency fixes, and passes `make verify`
plus the full PostgreSQL suite. Its exact-SHA record is
[here](evidence/2026-09-30-integrated-candidate-746400a-requalification.md);
browser and signed SDK/App artifact identities are not yet available for this
candidate.
Supplemental candidate `d35e909c6d3fbd1f7062c8c2aec0c744cc5e3b8b` adds PR
#92's declared-query aggregate implementation and has exact-source Vite/API
PostgreSQL browser evidence plus repository, Postgres, and artifact gates in
[its W5.2 record](evidence/2026-09-29-w5-2-integrated-candidate-d35e909.md).
This targeted qualification does not freeze `d35e909` as the release
candidate or transfer the full C6 matrix from `60f6e6f`.
The earlier candidate's broader partial requalification is
[here](evidence/2026-09-29-integrated-candidate-530b537-requalification.md).
Neither note is a completed C6 ledger. The next frozen candidate must name
the immutable Git revision and the hashes of the Core wheel, SDK wheel, and
independent App archive before every mandatory gate can be recorded as passed.
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
| Git revision | Full immutable SHA | No frozen release candidate. Historical automated rows below name `9269ad1783bf83acff90fe1accf3ae19ae961d53`; latest broad integration candidate is `60f6e6fa4c22181ba17bba7b7e2d6001678cafc8`, with targeted W5.2 qualification on supplemental `d35e909c6d3fbd1f7062c8c2aec0c744cc5e3b8b`. |
| Core wheel | Filename + SHA-256 | Rebuilt and retained for `60f6e6f`: `integral_core-0.1.1rc11-py3-none-any.whl`, SHA-256 `d5a6dfdc3d44cb365551aec87ceda788a12be18b919c2d1671020bb651647134`. |
| SDK wheel | Filename + SHA-256 | Rebuilt and retained for `60f6e6f`: `integral_sdk-0.2.0-py3-none-any.whl`, SHA-256 `bc0178fc67a98d4a30b7f12cc72e431b3f2dbe464e44820ee223ae3022452fab`. |
| Independent App archive | Filename + SHA-256 + signature key identity | Rebuilt and retained for `60f6e6f`: `asset-register-1.0.0.tar.gz`, SHA-256 `741314806006cb8ef4e021429c7a995c651ed65203a8d847f0f26864115cec81`; verification public-key file SHA-256 `375eeaccad129f543913f1556021d81d0f033f0fc76e24c189835683f4620da7`; Core signature verification passed. |
| Container images | Image digests for API and web | Local Docker IDs retained: API `sha256:6da410eb64527319d7eb17069fc93ac76c8c3165893ad8a95168c0058e59e008`; web `sha256:d2366180ce6e490f31a550277486e307f862dc3605e98412958e09dfd909fa73`. No registry RepoDigests are available. |
| Python, Node, Docker, Postgres | Exact versions | Python 3.14.3, Node v23.10.0, PostgreSQL 16.14 on `60f6e6f`; local API/web image IDs are recorded above, with no registry RepoDigests. |
| Configuration | Non-secret settings digest; model/provider state | Local Ollama `gemma4:e2b` available and CLI-smoked on `60f6e6f`; no frozen settings digest or full app chat journey. |
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
| Browser acceptance | Ordinary signed-in journeys on the candidate deployment | Experience | Exact `530b537` browser check passed fresh signup, empty-workspace landing, first-Track creation and persistence after reload, and workspace-effective catalogue rendering (16 skills, 122 tools) on the PostgreSQL-backed local candidate API; focused dashboard denial/padding check also passed. Exact `60f6e6f` recheck confirmed Guyana Payroll App focus selection and readable undeclared-query denial on an empty dashboard. Supplemental exact-source `d35e909` browser recheck displayed the declared Asset Register aggregate `Available assets: 3` from its PostgreSQL QA fixture using both candidate Vite and API. These are targeted browser proofs, not a complete C6 matrix or same-SHA release candidate. | [2026-09-29 candidate requalification](evidence/2026-09-29-integrated-candidate-530b537-requalification.md); [60f6e6f App-focus and dashboard recheck](evidence/2026-09-29-60f6e6f-app-focus-dashboard-recheck.md); [d35e909 W5.2 candidate evidence](evidence/2026-09-29-w5-2-integrated-candidate-d35e909.md) |
| Transport parity | UI, extension HTTP, resident, and MCP operation/query journeys | Execution | Partial. Open PR #92 now adds same-fixture contracts comparing the dispatcher, authenticated extension HTTP route, resident tool dispatch, MCP governed-query dispatch, and dashboard preview across the same three Entries; it also calls the mounted authenticated Streamable HTTP MCP endpoint with the same query and verifies matching rows, references, and workspace scope. The browser proof remains on supplemental candidate `d35e909`, and no single frozen candidate yet has the full browser/API/resident/MCP journey, deployed external network trace, or durable read-receipt parity on every surface. | [PR #92 same-fixture parity test and evidence](https://github.com/V75inc/integral-core/pull/92); [exact-source W5.2 browser recheck](evidence/2026-09-29-w5-2-integrated-candidate-d35e909.md) |
| Restore drill | Fresh deployment restore of a populated fixture | Persistence | Focused drill and full Postgres backend suite pass on `bbd55db`; full Postgres suite and corrected CI-faithful Postgres lane pass on descendant candidate `60f6e6f`. | [60f6e6f candidate requalification](evidence/2026-09-29-integrated-candidate-60f6e6f-requalification.md) |
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
| A12 | Independent App has identical enforcement across UI, HTTP, resident, MCP | Extension / execution | Partial evidence only; exact-source W5.2 dashboard query now visible in UI, while cross-surface equivalence remains unproven | Four-surface operation and query receipts with aligned outputs and policy outcomes |
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
program and work-package exits.
