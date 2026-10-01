# Core acceptance ledger

**Purpose:** the single release-evidence record for Integral Core.

**Status:** **C6 is not complete.** The frozen combined repair candidate is
`f5c853c6577db3db576a2fbe8865d3023d0f8a42` (PRs #97 + #99). Exact-SHA
registry deployment and selected browser/API probes passed, but A01, A04,
A05–A08, A10–A15 have explicit failed dispositions; A02, A03, and A09 pass.
Independent architecture review and Product Owner acceptance have not been
requested for this packet. See the [frozen-candidate evidence](evidence/2026-10-01-c6-file-volume-and-resident.md).
**Last full ledger table below:** `bb3b1e0b11bc80db697d7187dc9b7e2212789dc1`;
see its [historical registry/browser record](evidence/2026-09-30-c6-merge-qualification.md).
The table is not a qualification of the changed repair candidate.
The [A04 repair history](evidence/2026-10-01-a04-create-scope-repair.md)
contains earlier selected local probes. The frozen candidate's latest
registry/browser run is now recorded in the C6 evidence file; only the row
disposition above applies to this matrix.
**Supported topology for qualification:** Core API and web bundle with
Postgres. SQLite and JSON stores support local development and reconciliation;
they do not establish multi-worker command, lease, or recovery guarantees.

This ledger supersedes the claim-oriented tables in
[RELEASE_CANDIDATE.md](RELEASE_CANDIDATE.md). The test-to-criterion mapping
remains in [ACCEPTANCE_TEST_MAP.md](ACCEPTANCE_TEST_MAP.md). A test can be
useful development evidence without qualifying the frozen candidate.

## Latest candidate selected evidence

The [frozen candidate record](evidence/2026-10-01-c6-file-volume-and-resident.md)
contains exact f5 artifact identities, registry images, fresh deployment,
browser evidence, and an explicit result for every A01–A16 row. The historical
table below belongs only to `bb3b1e0`.

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
| Git revision | Full immutable SHA | `f5c853c6577db3db576a2fbe8865d3023d0f8a42` |
| Core wheel | Filename + SHA-256 | `integral_core-0.1.1rc11-py3-none-any.whl`, `be5abfe5b9ac11183b8b1ec30a5a998f90c524a558767548ecd1422ada011222` |
| SDK wheel | Filename + SHA-256 | `integral_sdk-0.2.0-py3-none-any.whl`, `c73e283c4e6d3f253e6780477cf7b73aa3826f570bef537570d476c87917feed` |
| Signed Asset Register archive | Filename + SHA-256 + signing public-key SHA-256 | `asset-register-1.0.0.tar.gz`, `e17ea7497058280e5b7b3882f73b5bd069f9590bd54e910fb27fa95c3b1da591`; key file `552b5f12ad2758bb0695ad03093b4e44a653e9546e64a4fb7d0ec4e347bec93d` |
| Container images | Immutable API and web image digests | API `ghcr.io/v75inc/integral-core-qualification-api@sha256:a1f446aa32323da13b70506a79226a1e513e2146bf5f1c0b2dd64e27f10c2c6e`; web `ghcr.io/v75inc/integral-core-qualification-web@sha256:5156a5e63a51a2c7bef302c1eac6407ec41830242fbc0552e0c81024e68bc1ee` |
| Registry and deployment run | Exact SHA, successful build, digest pull, fresh Postgres/files deployment, browser | [Run 36941117498](https://github.com/V75inc/integral-core/actions/runs/36941117498), source `f5c853c`; Docker build/pull/deploy and Chromium all ran, not skipped |
| Configuration | Core-only and provider state | `INTEGRAL_CORE_ONLY=1`; no global model provider keys supplied to the registry deployment |
| Fresh Postgres test volume | Isolated local test lane | Fresh `integral-c6-f5-pgdata` volume; full `make test-postgres` completed with explicit skips listed in candidate evidence |

## Mandatory gates

C6 fills this table once, for one frozen SHA. A green run on another revision stays outside the table. Skipped is not a pass. The external live-model exam is not a row here.

| Gate | Command or journey | Owner | Candidate result | Evidence to retain |
| --- | --- | --- | --- | --- |
| Repository gate | `make verify` | Release | **Pass on the same executable tree**; f5 adds only the A04 evidence document over 994622a | A04 repair record and CI checks |
| CI-faithful smoke | `make verify-ci` | Release | **Pass on the same executable tree**; docs-only delta on f5 | CI checks |
| Core-only boundary | `INTEGRAL_CORE_ONLY=1` registry deployment | Platform | **Pass for deployment boot and generic browser journey** | Run 36941117498 and browser artifact |
| Postgres proof | `make test-postgres` | Persistence | **Pass** on fresh isolated volume; explicit skips retained | Current turn result; see row notes |
| Built Core, SDK, App | `make verify-independent-artifacts` | Release / SDK / Extension | **Pass**; exact SHA-256 identities recorded above | Current turn result; hashes in identity table |
| Browser acceptance | Fresh registry digest deployment | Experience | **Pass for signup, App, Track, Entry, global Tracks; fail A01 until generic View is added** | Run 36941117498 browser artifact |
| A04 scope/revocation | Two-user browser/API, source tests | Identity / execution | **Fail overall**; listed exact-image HTTP checks pass, resident/MCP write and persisted-inventory boundaries remain open | Current candidate evidence |
| Registry/deployment | GHCR digest build, pull, and fresh deployment | Release | **Pass** on exact f5 SHA; no Docker verification step skipped | Run 36941117498 |
| Restore drill | Populated dump plus `/data` archive to scratch | Persistence | **Fail**; not run on this candidate's images | Current candidate evidence |
| Human review | New architecture and Product Owner decisions | Independent reviewer / Product Owner | **Not requested yet** | Request after this packet is committed and pushed |

All rows refer to `bb3b1e0b11bc80db697d7187dc9b7e2212789dc1`, run 2026-09-30. Logs and immutable registry identities are indexed in the [final record](evidence/2026-09-30-c6-merge-qualification.md).

## Finish-line acceptance matrix

| ID | Required outcome | Responsible area | Candidate status | Evidence requirement |
| --- | --- | --- | --- | --- |
| A01 | Core installs cleanly and boots without commercial Apps | Release / platform | **FAIL** — generic saved View journey missing | Frozen-candidate evidence |
| A02 | Ordinary Core use remains available with the model provider unavailable | Platform / experience | **PASS** — provider-free registry browser journey | Run 36941117498 artifact |
| A03 | Module boundary violations fail the build | Platform | **PASS** — exact-tree guard | `.ci/module_boundary_check.sh` |
| A04 | Bad scope and revoked access fail at every effect boundary | Identity / execution | **FAIL** — live resident/MCP writes and effect/list assertions incomplete | Frozen-candidate evidence |
| A05 | Fields stay stable across rename, nulls, and platform/business collisions | Information | **FAIL** — cross-surface acceptance fixture absent | Frozen-candidate evidence |
| A06 | Concurrent command plus crash creates one effect and no duplicate receipt | Execution / persistence | **FAIL** — no candidate crash/concurrency trace | Frozen-candidate evidence |
| A07 | Approved revision executes once; correction, expiry, and cancellation report accurately | Execution / resident | **FAIL** — full candidate lifecycle trace absent | Frozen-candidate evidence |
| A08 | Build resumes after restart without duplicate objects | Applications / execution | **FAIL** — candidate restart/ledger trace absent | Frozen-candidate evidence |
| A09 | Exact query and every rendered view agree above page limits and date boundaries | Query / experience | **PASS** — backend 2 tests and frontend 4 tests rerun on f5 tree | Candidate evidence and [parity details](evidence/2026-09-21-a09-query-view-parity.md) |
| A10 | Populated schema alteration preserves bindings or fails before unsafe change | Information / applications | **FAIL** — candidate migration proof absent | Frozen-candidate evidence |
| A11 | External unknown outcomes reconcile before retry | Execution | **FAIL** — candidate reconciliation trace absent | Frozen-candidate evidence |
| A12 | Independent App has identical enforcement across UI, HTTP, resident, MCP | Extension / execution | **FAIL** — artifact and Postgres replay tests pass, but no one-receipt/no-duplicate four-surface trial on exact images | Frozen-candidate evidence |
| A13 | Upgrade preserves customization; pause/uninstall fence capabilities and work | Applications / extension | **FAIL** — candidate lifecycle drill absent | Frozen-candidate evidence |
| A14 | Restore reproduces records, edges, attachments, package identity, and work | Persistence / release | **FAIL** — populated database and `/data` archive not restored on candidate images | Frozen-candidate evidence |
| A15 | Active documentation is coherent, linked, and executable | Documentation / all owners | **FAIL** — anchors, external links, executable commands, independent trial incomplete | Frozen-candidate evidence |
| A16 | External live-model exam meets its own budgets | Intelligence / release | **OUTSIDE CORE** — separate and non-blocking | Kept outside this ledger |

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
- The extracted-archive journey and populated restore are historical evidence
  from earlier candidates; the complete A14 fixture and recovery matrix is
  still open on the frozen f5 candidate.
- The external live-model exam and human acceptance are pending. A model miss does not change Core.

See [CORE_FINISH_STATUS.md](CORE_FINISH_STATUS.md) for the ordered build
program and work-package exits.
