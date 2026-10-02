# Core acceptance ledger

**Purpose:** the single release-evidence record for Integral Core.

**Status:** **Row proofs passed; C6 is not yet one image SHA.** A02, A03, and
A09 stay pass. A01, A04, the core HTTP/resident/MCP read, and A14 passed on
the running main smoke image
`sha256:acb3e002ec859e54ae14bb6e740bdedf15be0fca4ad02e79c1b51d6c8d647a79`.
A05–A08, A10–A13, and A15 passed as source proofs on this branch. A16 stays
outside Core. Publication is not authorized until those source proofs are
rebuilt into that image and the browser and restore drills are repeated.
See the [main qualification evidence](evidence/2026-10-01-c6-file-volume-and-resident.md).

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

**Product Owner (2026-10-02, first decision):** not accepted for `7965594`.

**Follow-up proofs (2026-10-02):** the failed rows were run. Browser and restore evidence is the smoke image above. Source proofs are in this branch. Do not publish until one image is built from this source and those drills are repeated.

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
| A01 | Core installs cleanly and boots without commercial Apps | Release / platform | **PASS on smoke image** — signup, App, Track, Entry, and saved table View reopened in Chromium | `/tmp/c6-browser-evidence/browser-smoke.json` `savedView` |
| A02 | Ordinary Core use remains available with the model provider unavailable | Platform / experience | **PASS** — provider-free registry browser journey | Run 36945949667 artifact |
| A03 | Module boundary violations fail the build | Platform | **PASS** — exact-tree guard | `.ci/module_boundary_check.sh` |
| A04 | Bad scope and revoked access fail at every effect boundary | Identity / execution | **PASS on smoke image** — foreign Track create 403, owner 200, revocation, resident and MCP writes rejected, no denied entry persisted | `/tmp/c6-browser-evidence/browser-smoke.json` |
| A05 | Fields stay stable across rename, nulls, and platform/business collisions | Information | **PASS** — one projection for form, view, dashboard, and agent query | `tests/contracts/test_c6_row_proofs.py` |
| A06 | Concurrent command plus crash creates one effect and no duplicate receipt | Execution / persistence | **PASS** — injected crash rolls back; retry commits one node and one receipt | `test_a06_crash_then_retry_commits_one_effect_and_one_receipt` on Postgres |
| A07 | Approved revision executes once; correction, expiry, and cancellation report accurately | Execution / resident | **PASS** — consume once, correction error, expiry, revoke | `test_a07_approval_executes_once_and_reports_correction_expiry_cancel` |
| A08 | Build resumes after restart without duplicate objects | Applications / execution | **PASS** — ledger resume keeps ids; install of an active bundle stays idempotent | `test_a08_restart_resumes_the_same_requirement_ledger`, `test_install_idempotent_when_bundle_already_active` |
| A09 | Exact query and every rendered view agree above page limits and date boundaries | Query / experience | **PASS** — backend 2 tests and frontend 4 tests rerun on main source tree | Candidate evidence and [parity details](evidence/2026-09-21-a09-query-view-parity.md) |
| A10 | Populated schema alteration preserves bindings or fails before unsafe change | Information / applications | **PASS** — unmigrated impact leaves the record snapshot unchanged; declared ops clear the gate | `test_a10_unmigrated_populated_change_keeps_records` |
| A11 | External unknown outcomes reconcile before retry | Execution | **PASS** — unknown waits; an observed result is applied once; delivered is not retried | `reconcile_external_outcome` and `test_a11_unknown_external_outcome_reconciles_before_retry` |
| A12 | Independent App has identical enforcement across UI, HTTP, resident, MCP | Extension / execution | **PASS** — Asset Register query matches dashboard, HTTP, resident, and MCP; browser entry read matched HTTP, resident, and MCP on the smoke image | `test_declared_asset_query_matches_dashboard_http_resident_and_mcp`; browser `crossSurfaceRead` |
| A13 | Upgrade preserves customization; pause/uninstall fence capabilities and work | Applications / extension | **PASS** — local track stays in the upgrade preview; pause/resume and extracted-archive lifecycle passed | `test_a13_upgrade_preview_keeps_local_customization`, `test_pause_then_resume`, `test_extracted_asset_register_lifecycle_revokes_and_restores_resident_tool` |
| A14 | Restore reproduces records, edges, attachments, package identity, and work | Persistence / release | **PASS on smoke image** — scratch database and `/data` volume served the same attachment bytes | `/tmp/c6-a14-evidence/restore.json` sha256 `64a899b915f0c3d5414cb97259992696a6568a35815bc57f1d846bc9a68a2e19` |
| A15 | Active documentation is coherent, linked, and executable | Documentation / all owners | **PASS for local targets and one command** — 532 relative targets, `make -n verify-ci` exit 0. Heading anchors and an independent author trial are still outside this script | `scripts/c6_active_doc_check.py` |
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
