# Core acceptance ledger

**Purpose:** the single release-evidence record for Integral Core.

**Status:** **C6 is decided and not passed** for merged `main`
`7965594aafccca23d945e40663d254dd693c54e2` (PRs #97 + #99). The record is
complete: every A01–A15 row is pass or fail, A16 stays outside Core, and the
architecture review and Product Owner decision below are recorded. A02, A03,
and A09 pass. A01, A04–A08, and A10–A15 fail. Publication is not authorized.
A later docs or setup commit does not open a new candidate. The next C6
attempt starts only when the failed journeys below are actually run, and it
must name a new frozen SHA. See the [main qualification evidence](evidence/2026-10-01-c6-file-volume-and-resident.md).

## Decision — 2026-10-02

**Executable candidate:** `7965594aafccca23d945e40663d254dd693c54e2`. Registry
run [36945949667](https://github.com/V75inc/integral-core/actions/runs/36945949667)
built that SHA. PR #101 changes bootstrap, Compose, and Fernet key generation.
Those are operational fixes. They are not this candidate, and they do not
supply the failed journeys.

**Architecture review:** The #97 attachment-volume and installed-App binding
repairs and the #99 workspace-scope and revocation repairs are on this main
SHA. The matrix fails are missing required proofs, not a request for another
ledger rewrite. Contract tests that already exist for field identity, work
recovery, migration rejection, and App lifecycle were not accepted as
substitutes for the cross-surface, crash, restart, four-surface, and restore
journeys named in the matrix.

**Product Owner:** not accepted. Do not publish.
**Last full ledger table below:** `bb3b1e0b11bc80db697d7187dc9b7e2212789dc1`;
see its [historical registry/browser record](evidence/2026-09-30-c6-merge-qualification.md).
The table is historical and does not qualify current main.
The [A04 repair history](evidence/2026-10-01-a04-create-scope-repair.md)
contains earlier selected local probes. The current main registry/browser run
is recorded in the C6 evidence file; only the row disposition above applies to
this matrix.
**Supported topology for qualification:** Core API and web bundle with
Postgres. SQLite and JSON stores support local development and reconciliation;
they do not establish multi-worker command, lease, or recovery guarantees.

This ledger supersedes the claim-oriented tables in
[RELEASE_CANDIDATE.md](RELEASE_CANDIDATE.md). The test-to-criterion mapping
remains in [ACCEPTANCE_TEST_MAP.md](ACCEPTANCE_TEST_MAP.md). A test can be
useful development evidence without qualifying the exact candidate.

## Latest candidate selected evidence

The [main candidate record](evidence/2026-10-01-c6-file-volume-and-resident.md)
contains exact main artifact identities, registry images, fresh deployment,
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
| Git revision | Full immutable SHA | `7965594aafccca23d945e40663d254dd693c54e2` |
| Core wheel | Filename + SHA-256 | `integral_core-0.1.1rc11-py3-none-any.whl`, `3365f76e315a52ed89a637ea805b2ac521c4c5cd786840952f1f86d1f3109b37` |
| SDK wheel | Filename + SHA-256 | `integral_sdk-0.2.0-py3-none-any.whl`, `bb0577e7e77a07ca444c947bf95ad886ce7c6c9819b98727249ae8995c3c3a48` |
| Signed Asset Register archive | Filename + SHA-256 + signing public-key SHA-256 | `asset-register-1.0.0.tar.gz`, `0089e88cfd2329423a0ecd9a0b45156c87de05bbe003f79873f79a0c60672989`; key file `66f6e9b21103e52e8559446ec11a5579763ac62194a64bb6fa59cd553f2659e9` |
| Container images | Immutable API and web image digests | API `ghcr.io/v75inc/integral-core-qualification-api@sha256:4065dd3a993051e8a2bd6f910102f84d1b5f07b232e8bfda58d132506456e5a7`; web `ghcr.io/v75inc/integral-core-qualification-web@sha256:f1c110a9576ab8853842388b4bfa418860da50d4a679890ddc231db4d97c9ff2` |
| Registry and deployment run | Exact SHA, successful build, digest pull, fresh Postgres/files deployment, browser | [Run 36945949667](https://github.com/V75inc/integral-core/actions/runs/36945949667), source `7965594`; Docker build/pull/deploy and Chromium all ran, not skipped |
| Configuration | Core-only and provider state | `INTEGRAL_CORE_ONLY=1`; no global model provider keys supplied to the registry deployment |
| Fresh Postgres test volume | Isolated local test lane | Fresh isolated Postgres service on port 15435 with per-worker databases; full selected Postgres lane and `make verify` completed, with explicit skips in candidate evidence |

## Mandatory gates

C6 fills this table once, for one frozen SHA. A green run on another revision stays outside the table. Skipped is not a pass. The external live-model exam is not a row here.

| Gate | Command or journey | Owner | Candidate result | Evidence to retain |
| --- | --- | --- | --- | --- |
| Repository gate | `make verify` | Release | **Pass with caveat**; unstaged changes made index-based pre-commit guards vacuous | A04 repair record and CI checks |
| CI-faithful smoke | `make verify-ci` | Release | **Pass** on merged `main` source | CI checks |
| Core-only boundary | `INTEGRAL_CORE_ONLY=1` registry deployment | Platform | **Pass for deployment boot and generic browser journey** | Run 36945949667 and browser artifact |
| Postgres proof | Fresh Postgres full backend lane, `-n 2`, `not domain_app and not slow` | Persistence | **Pass**; optional vector/Atlas/benchmark and unseeded-library cases skipped explicitly | Current turn result; see row notes |
| Built Core, SDK, App | `make verify-independent-artifacts` | Release / SDK / Extension | **Pass**; exact SHA-256 identities recorded above | Current turn result; retained artifacts at `/tmp/c6-main-artifacts/` |
| Browser acceptance | Fresh registry digest deployment | Experience | **Pass for signup, App, Track, Entry, global Tracks; local source stack also created a Dashboard. A01 remains open until a saved generic View is evidenced on registry images.** | Run 36945949667 browser artifact; local smoke tab |
| A04 scope/revocation | Two-user browser/API, HTTP/resident/MCP effects | Identity / execution | **Fail overall**; full additional probes passed on local source-built `main`, but not yet on the exact registry digests | Current candidate evidence and `/tmp/c6-main-evidence-a04-rerun/browser-smoke.json` |
| Registry/deployment | GHCR digest build, pull, and fresh deployment | Release | **Pass** on exact `main` SHA; no Docker verification step skipped | Run 36945949667 |
| Restore drill | Populated dump plus `/data` archive to scratch | Persistence | **Fail**; not run on this candidate's images | Current candidate evidence |
| Human review | Architecture and Product Owner decisions | Independent reviewer / Product Owner | **Complete — not accepted** (2026-10-02) | Decision section above |

The mandatory gates above are for `7965594`. The `bb3b1e0` run remains historical in [its own record](evidence/2026-09-30-c6-merge-qualification.md).

## Finish-line acceptance matrix

| ID | Required outcome | Responsible area | Candidate status | Evidence requirement |
| --- | --- | --- | --- | --- |
| A01 | Core installs cleanly and boots without commercial Apps | Release / platform | **FAIL** — generic Dashboard created locally; saved generic View journey on registry images missing | Main candidate evidence |
| A02 | Ordinary Core use remains available with the model provider unavailable | Platform / experience | **PASS** — provider-free registry browser journey | Run 36945949667 artifact |
| A03 | Module boundary violations fail the build | Platform | **PASS** — exact-tree guard | `.ci/module_boundary_check.sh` |
| A04 | Bad scope and revoked access fail at every effect boundary | Identity / execution | **FAIL** — HTTP/resident/MCP writes, inventories, private list/read, and public read-only probes pass on local source-built main; exact registry digest repetition remains | Main candidate evidence |
| A05 | Fields stay stable across rename, nulls, and platform/business collisions | Information | **FAIL** — cross-surface acceptance fixture absent | Main candidate evidence |
| A06 | Concurrent command plus crash creates one effect and no duplicate receipt | Execution / persistence | **FAIL** — no candidate crash/concurrency trace | Main candidate evidence |
| A07 | Approved revision executes once; correction, expiry, and cancellation report accurately | Execution / resident | **FAIL** — full candidate lifecycle trace absent | Main candidate evidence |
| A08 | Build resumes after restart without duplicate objects | Applications / execution | **FAIL** — candidate restart/ledger trace absent | Main candidate evidence |
| A09 | Exact query and every rendered view agree above page limits and date boundaries | Query / experience | **PASS** — backend 2 tests and frontend 4 tests rerun on main source tree | Candidate evidence and [parity details](evidence/2026-09-21-a09-query-view-parity.md) |
| A10 | Populated schema alteration preserves bindings or fails before unsafe change | Information / applications | **FAIL** — candidate migration proof absent | Main candidate evidence |
| A11 | External unknown outcomes reconcile before retry | Execution | **FAIL** — candidate reconciliation trace absent | Main candidate evidence |
| A12 | Independent App has identical enforcement across UI, HTTP, resident, MCP | Extension / execution | **FAIL** — artifact and Postgres replay tests pass, but no one-receipt/no-duplicate four-surface trial on exact images | Main candidate evidence |
| A13 | Upgrade preserves customization; pause/uninstall fence capabilities and work | Applications / extension | **FAIL** — candidate lifecycle drill absent | Main candidate evidence |
| A14 | Restore reproduces records, edges, attachments, package identity, and work | Persistence / release | **FAIL** — populated database and `/data` archive not restored on candidate images | Main candidate evidence |
| A15 | Active documentation is coherent, linked, and executable | Documentation / all owners | **FAIL** — anchors, external links, executable commands, independent trial incomplete | Main candidate evidence |
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
- The extracted-archive journey and populated restore from earlier candidates
  do not prove A14 on `7965594`. A14 stays failed.
- The external live-model exam stays outside Core. Human acceptance of this
  candidate is recorded above as not accepted. A model miss does not change Core.

See [CORE_FINISH_STATUS.md](CORE_FINISH_STATUS.md) for the ordered build
program and work-package exits.
