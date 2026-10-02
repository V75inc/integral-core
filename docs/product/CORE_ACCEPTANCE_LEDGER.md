# Core acceptance ledger

**Purpose:** the single release-evidence record for Integral Core.

**Status:** **C6 is not complete.** The exact registry/browser candidate is
`e40824686995ba8ebf793622e83770bc80d0ff2b`. A01–A04, A09, and A11 pass.
A05–A08, A10, and A12–A15 remain failed because their proofs do not meet the
stated acceptance conditions. A16 stays outside Core. Architecture review and
a new Product Owner decision are pending; publication remains separate.

The three documentation files staged in the local `codex/c6-main-qualification`
checkout described the older `7965594` packet. This reconciliation keeps those
same three files as the C6 record and updates them to `e408246` plus the A11
delivery proof. It does not restore the earlier f5 or da33 status text.

## Decision — 2026-10-02

**Executable candidate:** `7965594aafccca23d945e40663d254dd693c54e2`. Registry
run [36945949667](https://github.com/V75inc/integral-core/actions/runs/36945949667)
built that SHA. PR #101 changes bootstrap, Compose, and Fernet key generation.
Those are operational fixes. They are not this candidate, and they do not
supply the failed journeys.

**Historical architecture review:** The #97 attachment-volume and installed-App binding
repairs and the #99 workspace-scope and revocation repairs are on this main
SHA. The matrix fails are missing required proofs, not a request for another
ledger rewrite. Contract tests that already exist for field identity, work
recovery, migration rejection, and App lifecycle were not accepted as
substitutes for the cross-surface, crash, restart, four-surface, and restore
journeys named in the matrix.

**Product Owner (2026-10-02, first decision):** not accepted for `7965594`.

**Candidate `e408246` (2026-10-02):** registry run [36973609020](https://github.com/V75inc/integral-core/actions/runs/36973609020) built, digest-pulled, and deployed these exact images on a fresh Postgres and `/data` volume. Chromium passed signup, App, Track, Entry, saved View reopen, the A04 scope/revocation/effect probes, and generic HTTP/resident/MCP reads. The exact-candidate disposition is in the matrix below. Local restore and contract tests are supporting evidence only where they do not meet the row's end-to-end acceptance conditions.

**Last historical full ledger table:** `bb3b1e0b11bc80db697d7187dc9b7e2212789dc1`;
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

The [current candidate record](evidence/2026-10-01-c6-file-volume-and-resident.md)
contains exact artifact identities, registry images, fresh deployment, browser
evidence, and an explicit result for every A01–A16 row. The table below is
historical and belongs only to `bb3b1e0`.

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
| Git revision | Full immutable SHA | `e40824686995ba8ebf793622e83770bc80d0ff2b` |
| Core wheel | Filename + SHA-256 | `integral_core-0.1.1rc11-py3-none-any.whl`, `1324e51892e62e3c9eea327d73c3bb0232521faade1966cda68014ee3a63e08f` |
| SDK wheel | Filename + SHA-256 | `integral_sdk-0.2.0-py3-none-any.whl`, `332594454623815532e826bd4aa5c66b08ebb91cc97027e6fea33aa027cf20ad` |
| Signed Asset Register archive | Filename + SHA-256 + signing public-key SHA-256 | `asset-register-1.0.0.tar.gz`, `64fd82b5adc6d47716aad054ceb6405ea493ab7107cd09729ab4a249dfc093f1`; key `fc3a816521fd733a772a0996bbe8efc7532b1731f3b5f027708bc22843ddcb07` |
| Container images | Immutable API and web image digests | API `ghcr.io/v75inc/integral-core-qualification-api@sha256:484a6fbe716b93a9c062cc40518b56b0920399a60fa7dda2ade2f078d7cd9bc6`; web `ghcr.io/v75inc/integral-core-qualification-web@sha256:eeb693353b1fc8d13a37e72950f4dbb00e1eb6d1e3705eba13f89078f14bf781` |
| Registry and deployment run | Exact SHA, successful build, digest pull, fresh Postgres/files deployment, browser | [Run 36973609020](https://github.com/V75inc/integral-core/actions/runs/36973609020); both published images were pulled by digest, deployed with fresh named volumes, and exercised in Chromium |
| Configuration | Core-only and provider state | `INTEGRAL_CORE_ONLY=1`; no global model provider keys supplied to the registry deployment |
| Fresh Postgres test volume | Isolated local test lane | CI `test-postgres` passed on `e408246`; registry run used a separate fresh Postgres volume |

## Mandatory gates

C6 fills this table once, for one frozen SHA. A green run on another revision stays outside the table. Skipped is not a pass. The external live-model exam is not a row here.

| Gate | Command or journey | Owner | Candidate result | Evidence to retain |
| --- | --- | --- | --- | --- |
| CI gate | Backend, frontend, independent artifacts, Postgres | Release | **Pass** on `e408246`; no failed checks | [CI run 36972185880](https://github.com/V75inc/integral-core/actions/runs/36972185880) |
| Core-only boundary | `INTEGRAL_CORE_ONLY=1` registry deployment | Platform | **Pass**; no global provider keys supplied | Run 36973609020 deployment artifact |
| Postgres proof | Fresh Postgres backend lane | Persistence | **Pass** in CI; registry deployment also used a fresh named Postgres volume | CI run 36972185880 and run 36973609020 |
| Built Core, SDK, App | Independent artifact gate and candidate hashes | Release / SDK / Extension | **Pass**; hashes in candidate identity table; CI artifact gate passed | CI run 36972185880; local exact-source builds recorded in candidate evidence |
| Browser acceptance | Fresh registry digest deployment | Experience | **Pass** for signup, App, Track, Entry, saved View reopen, and global Tracks | Run 36973609020 `browser-smoke.json` |
| A04 scope/revocation | Two-user browser/API, HTTP/resident/MCP effects | Identity / execution | **Pass** on exact registry digests | Run 36973609020 `browser-smoke.json` |
| Registry/deployment | GHCR digest build, pull, and fresh deployment | Release | **Pass** on exact `e408246`; image verification was not skipped | Run 36973609020 |
| Restore drill | Populated dump plus `/data` archive to scratch using candidate image digests | Persistence | **Fail**; only local-image restore evidence exists | Current candidate evidence |
| Human review | New architecture review and Product Owner decision | Independent reviewer / Product Owner | **Pending**; prior rejection applies to `7965594` only | New review required for `e408246` |

The candidate-specific gates above refer to `e408246`. The `bb3b1e0` acceptance table below remains historical in [its own record](evidence/2026-09-30-c6-merge-qualification.md).

## Finish-line acceptance matrix

| ID | Required outcome | Responsible area | Candidate status | Evidence requirement |
| --- | --- | --- | --- | --- |
| A01 | Core installs cleanly and boots without commercial Apps | Release / platform | **PASS** — signup, generic App/Track/Entry, saved table View reopened on exact registry digests | Run 36973609020 `browser-smoke.json` |
| A02 | Ordinary Core use remains available with the model provider unavailable | Platform / experience | **PASS** — registry deployment used Core-only mode without global provider keys; browser created and reopened generic records | Run 36973609020 deployment and browser artifacts |
| A03 | Module boundary violations fail the build | Platform | **PASS** — guard ran in CI on exact source SHA | Run 36972185880 backend check |
| A04 | Bad scope and revoked access fail at every effect boundary | Identity / execution | **PASS** — foreign scope 403, owner 200, revoked private reads/list and Entry create denied, public read-only control, resident/MCP writes denied with no persisted Entry | Run 36973609020 `browser-smoke.json` |
| A05 | Fields stay stable across rename, nulls, and platform/business collisions | Information | **FAIL** — new helper returns one computed projection copied four times; it does not exercise the real form, view, dashboard, and agent-query paths | `backend/tests/contracts/test_c6_row_proofs.py::test_a05_rename_null_and_collision_agree_on_every_surface` |
| A06 | Concurrent command plus crash creates one effect and no duplicate receipt | Execution / persistence | **FAIL** — Postgres test injects a crash and retries, but does not race concurrent commands or assert one receipt across the concurrent/crash sequence | `backend/tests/contract/test_operation_execution_receipts_postgres.py::test_a06_crash_then_retry_commits_one_effect_and_one_receipt` |
| A07 | Approved revision executes once; correction, expiry, and cancellation report accurately | Execution / resident | **FAIL** — staging-token state is tested in memory; no corrected approved revision is executed with one durable effect/receipt or continuation trace | `backend/tests/contracts/test_c6_row_proofs.py::test_a07_approval_executes_once_and_reports_correction_expiry_cancel` |
| A08 | Build resumes after restart without duplicate objects | Applications / execution | **FAIL** — helper deduplicates two in-memory ledgers; no process restart, persisted progress, or materialization proof | `backend/tests/contracts/test_c6_row_proofs.py::test_a08_restart_resumes_the_same_requirement_ledger` |
| A09 | Exact query and every rendered view agree above page limits and date boundaries | Query / experience | **PASS** — existing backend and frontend parity tests passed in CI on this source; the new projection helper is not used by those paths | Run 36972185880 and [parity details](evidence/2026-09-21-a09-query-view-parity.md) |
| A10 | Populated schema alteration preserves bindings or fails before unsafe change | Information / applications | **FAIL** — the test checks a pure rejection helper against synthetic data, not a populated database/schema change and rollback | `backend/tests/contracts/test_c6_row_proofs.py::test_a10_unmigrated_populated_change_keeps_records` |
| A11 | External unknown outcomes reconcile before retry | Execution | **PASS** — a persisted unknown outbox row is not delivered; after `provider_result` is recorded, delivery marks it delivered once and a second call does not deliver again | `tests/contract/test_operation_execution_receipts_postgres.py::test_a11_unknown_outbox_reconciles_before_retry` |
| A12 | Independent App has identical enforcement across UI, HTTP, resident, MCP | Extension / execution | **FAIL** — source tests compare query payloads and browser checks generic Core reads, but no one Asset Register operation/query is proved across UI, HTTP, resident, and MCP with the same receipt and no duplicate effect on these registry digests | `test_declared_asset_query_matches_dashboard_http_resident_and_mcp`; browser `crossSurfaceRead` |
| A13 | Upgrade preserves customization; pause/uninstall fence capabilities and work | Applications / extension | **FAIL** — separate preview and pause/resume tests do not prove a populated upgrade plus outstanding-work fencing through restart and uninstall | `test_a13_upgrade_preview_keeps_local_customization`; extracted archive lifecycle contract |
| A14 | Restore reproduces records, edges, attachments, package identity, and work | Persistence / release | **FAIL** — the retained scratch restore uses a local image; run 36973609020 did not restore a populated dump plus `/data` archive using its registry digests | `/tmp/c6-candidate-restore/restore.json` is supporting local evidence only |
| A15 | Active documentation is coherent, linked, and executable | Documentation / all owners | **FAIL** — relative targets and anchors were checked and `make -n verify-ci` only dry-ran; external URLs were skipped, no documented commands were executed, and no independent author trial is recorded | `scripts/c6_active_doc_check.py` output (531 relative targets, no missing local targets); required independent trial pending |
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

## Current candidate gaps

- One durable transaction/effect-receipt authority does not yet cover every
  UI, HTTP, resident, MCP, and extension operation.
- Query, form, saved-view, and dashboard semantics are not yet proven to use
  the same field and projection resolver above page limits.
- Schema publication, backfill, record updates, and App lifecycle evolution
  still require their durable-plan and recovery proof.
- The extracted-archive journey and populated restore from earlier candidates
  do not prove A14 on `e408246`; the exact-digest restore remains failed.
- A15 still needs external-link verification, actual documented-command
  execution, and an independent author trial.
- The new architecture review and Product Owner decision for `e408246` are
  pending. The earlier rejection applies only to `7965594`.
- The external live-model exam stays outside Core. A model miss does not change Core.

See [CORE_FINISH_STATUS.md](CORE_FINISH_STATUS.md) for the ordered build
program and work-package exits.
