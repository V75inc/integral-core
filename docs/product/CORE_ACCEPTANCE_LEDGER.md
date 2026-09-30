# Core acceptance ledger

**Purpose:** the single release-evidence record for Integral Core.

**Status:** **C6 is not complete.** Browser and deployed transport parity,
registry publication, and human/Product Owner decisions remain open.
**Current qualification candidate:** `b79fd057d502a42047d032af201771cbb375f3a4`.
The 2026-09-28 record below applies only to its earlier SHA and cannot close
this candidate. See the [2026-09-30 continuation](evidence/2026-09-30-c6-continuation.md).
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
| Git revision | Full immutable SHA | `b79fd057d502a42047d032af201771cbb375f3a4` (qualification in progress) |
| Core wheel | Filename + SHA-256 | `integral_core-0.1.1rc11-py3-none-any.whl`, `b9459f8db6914d9316261c57cd71fe3d1cce2b9bb3ba345787e6d09a779ecdd3` |
| SDK wheel | Filename + SHA-256 | `integral_sdk-0.2.0-py3-none-any.whl`, `4cd8b631e2e61bdc07950be03022111c91dce06ed7d669f100e8c15961268cad` |
| Independent App archive | Filename + SHA-256 + signature key identity | `asset-register-1.0.0.tar.gz`, `7c69f6c6f402b671ac10f994fd458adf025eccca391c5ac2d19abdd6be19fce6`; public-key file SHA-256 `dbb5b894e6a3cc1303fd413f011fca4023a6143ade173f9623469e941480ad75` |
| Container images | Image digests for API and web | API `sha256:a234409830b27ed73a32b0cdbc7c34aef231fbf0ce811c0529ae4375e1f42ca3`; web `sha256:9dda027a67f56ffd91b011f01c577f432e6e331261af86730826f1c7e8348d4a`; isolated Postgres `sha256:fa3d9bb7ee77f5c1f0bfb009a9df30243c040896825f3033b09a77101bb2ca95` |
| Python, Node, Docker, Postgres | Exact versions | Host Python 3.14.3, Node 23.10.0, Docker 28.2.2, Postgres 16.14; API image Python 3.11 |
| Configuration | Non-secret settings digest; model/provider state | `c0666a2ef611d9bcb8073281fc4a9c4d26d012b712cdef39b076667cffa78658`; provider credentials absent in browser candidate; disposable OAuth encryption key |
| Fixture / backup identity | Seed or backup digest and dataset version | Fresh synthetic UI-created account and first Track in isolated Compose project `integral-core-c6`; Postgres worker databases and temporary restore drill |

## Mandatory gates

C6 fills this table once, for one frozen SHA. A green run on another revision stays outside the table. Skipped is not a pass. The external live-model exam is not a row here.

| Gate | Command or journey | Owner | Candidate result | Evidence to retain |
| --- | --- | --- | --- | --- |
| Repository gate | `make verify` | Release | **Pass** on `c13db809` (2026-09-28) | [Candidate qualification](evidence/2026-09-28-c6-candidate-qualification.md), local repository log |
| CI-faithful smoke | `make verify-ci` | Release | **Pass**, included in the repository gate | Same candidate log |
| Core-only boundary | `make verify-core-only` | Platform | **Pass** on `c13db809` | Same candidate log |
| Contract lane | `make verify-contract` | Extension | **Pass** on `c13db809` | Same candidate log |
| Postgres proof | Postgres suite on isolated worker databases | Persistence | **Pass** on `c13db809`; two xdist workers and temporary restore drill | Same candidate log; `.qualification-evidence/2026-09-28T11-43-05.028741+00-00-postgres.log` |
| Built Core | `make verify-artifact` and `make verify-clean-install` | Release | **Pass** on `c13db809`; wheel imports and clean ASGI import verified | Same candidate log, Core wheel digest above |
| Built SDK | `make verify-sdk-artifact` | SDK | **Pass** on `c13db809` | Same candidate log, SDK wheel digest above |
| Independent App | `make verify-external-asset-register` | Extension | **Pass** on `c13db809`; extracted signed archive handler loaded | Same candidate log, archive digest above |
| Browser acceptance | Ordinary signed-in journeys on the candidate deployment | Experience | **Pass** for new-account first-Track creation, immediate list appearance, and reload persistence; navigation routes loaded | [Candidate qualification](evidence/2026-09-28-c6-candidate-qualification.md) |
| Transport parity | UI, extension HTTP, resident, and MCP operation/query journeys | Execution | **Partial.** Repair deployment passes typed HTTP mutation/replay, browser readback, and live authenticated HTTP/MCP query equality. Browser-origin mutation, live resident parity, and frozen repair-SHA qualification remain open. | [2026-09-30 continuation](evidence/2026-09-30-c6-continuation.md) |
| Restore drill | Restore a populated dump into a temporary database | Persistence | **Pass** in the candidate Postgres lane; graph counts and identity matched, scratch DB removed | Candidate qualification; Postgres lane log |
| Human review | Architecture and release review | Independent reviewer / Product Owner | **Pending** | Decision records in the [2026-09-30 review packet](evidence/2026-09-30-c6-continuation.md); no approval may be inferred |

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
program and work-package exits.
