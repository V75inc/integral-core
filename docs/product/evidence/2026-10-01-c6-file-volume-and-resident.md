# C6 file-volume and resident qualification history

## Current candidate

The current frozen candidate and complete technical evidence are in the
[2026-10-02 candidate packet](2026-10-02-c6-candidate-eff58c4.md). Candidate
`eff58c4d6d42c4a01c85177485a08c5e403ee64d` passes A01–A14 on exact registry
images. A15's independent author trial and the new Architecture and Product
Owner decisions remain pending; C6 is not complete.

The volume migration is in force: existing data formerly mounted at
`/app/integral_data` now mounts at `/data`, and new files are under
`/data/files`. An operator moving an old volume must repair ownership once
before attachment writes. The current candidate's fresh-volume restore proof
and hashes are retained in the packet and
[registry run 37013122424](https://github.com/V75inc/integral-core/actions/runs/37013122424).

All candidate-specific records below are historical; none supersedes the
current packet above.

## Historical candidate: `c16ea31` (superseded after A12 setup failure)

**Candidate source revision:** `c16ea316ef40e70b20a2b009268c8f61ee666238`
**Date:** 2026-10-02 UTC
**Disposition:** **C6 is not complete.** Local source and independent
Core/SDK/external-App artifact gates pass. A03, A05–A11, and A13 have local
passing evidence; A01, A02, A04, A12, A14, and A15 remain failed pending
exact-image deployment, browser, restore, or independent-author evidence.
A16 stays outside Core. Architecture and Product Owner decisions are pending;
publication remains separate. See the [candidate packet](2026-10-02-c6-candidate-c16ea31.md)
and [ledger](../CORE_ACCEPTANCE_LEDGER.md).

### Candidate identity

| Artifact | Identity |
| --- | --- |
| Git source | `c16ea316ef40e70b20a2b009268c8f61ee666238` |
| Core wheel | `integral_core-0.1.1rc11-py3-none-any.whl`, SHA-256 `0c2d8969f08ef5419aa52e0b155818bb4d4f1b452b41fde6a82e944989711682` |
| SDK wheel | `integral_sdk-0.2.0-py3-none-any.whl`, SHA-256 `e1bc5eee292a5307cb7305edb6578ed6b83043f605132d557c3ead781ced7ec0` |
| Signed Asset Register archive | `asset-register-1.0.0.tar.gz`, SHA-256 `a9914505fd6217b7634a52c86c1b6bac0ff3c909ef370aec28597084f1989420`; public-key file SHA-256 `a931d459da51f98830072060bd43ee82be58dec53f2bfb8e2a2bb051e3d2649e` |
| API / web images | **Pending** — exact-SHA registry build, clean digest pull, Docker verification, and browser run |
| Build environment | macOS arm64, CPython 3.11.15, `SOURCE_DATE_EPOCH=1790936902`; signing key removed after archive build; retained artifacts at `/tmp/c6-artifacts-c16ea31/` |

`make verify` and `make verify-independent-artifacts` passed on exact SHA
`c16ea31`. The only difference from `3ecb49b`, whose targeted fresh-Postgres
contracts passed, is the A12 workflow evidence-copy correction in
`.github/workflows/qualify-images.yml`. The workflow has not yet been pushed or
run, so no registry digests or exact-image browser/restore results are claimed.
It uses fresh Postgres and `/data` files volumes. Existing operator volumes
mounted at `/app/integral_data` now mount at `/data`; new attachments are under
`/data/files`. Operators moving an old volume must repair ownership once before
attachment writes.

### Candidate-specific rows

| Row | Result | Evidence / remaining work |
| --- | --- | --- |
| A01 | **FAIL** | Exact-SHA registry browser journey for first login, generic App/Track/Entry and saved View is pending. |
| A02 | **FAIL** | Exact-image provider-free `INTEGRAL_CORE_ONLY=1` browser journey is pending. |
| A03 | **PASS** | Exact-SHA `make verify` guards include module-boundary checks. |
| A04 | **FAIL** | Exact-digest two-user scope/revocation plus HTTP, resident, and MCP write-effect probes are pending. |
| A05–A11 | **PASS locally** | Source contracts and full source suites pass; fresh-Postgres contracts for A05–A08 and A10–A11 passed on `3ecb49b`, with unchanged application code on this workflow-only successor. |
| A12 | **FAIL** | Fresh-Postgres one-receipt contract passed on `3ecb49b`. Exact-image Asset Register UI operation and HTTP/resident/MCP receipt/effect parity browser trial is pending. |
| A13 | **PASS locally** | Populated App upgrade and scheduled-work fencing contract passed on `3ecb49b`; application code is unchanged on `c16ea31`. |
| A14 | **FAIL** | Exact-image populated Postgres plus `/data/files` archive restore and authenticated hash-matching download are pending. |
| A15 | **FAIL** | Independent author trial with anchors, external links, executable commands, and retained results is pending. |
| A16 | **OUTSIDE CORE** | External live-model qualification is out of scope for the Core matrix. |

## Historical candidate: `e408246`

**Candidate source revision:** `e40824686995ba8ebf793622e83770bc80d0ff2b`
**Date:** 2026-10-02 UTC
**Disposition:** **C6 is not complete.** A01–A04, A09, and A11 pass. A05–A08,
A10, and A12–A15 fail because their required evidence is missing or weaker
than the acceptance condition. A16 stays outside Core. The staged copies of
this file, the acceptance ledger, and the finish status in the local
`codex/c6-main-qualification` checkout are reconciled into this record rather
than restored as the older `7965594` packet. Independent architecture review
and a new Product Owner decision remain pending. Publication is separate.

### Candidate identity

| Artifact | Identity |
| --- | --- |
| Git source | `e40824686995ba8ebf793622e83770bc80d0ff2b` |
| Core wheel | `integral_core-0.1.1rc11-py3-none-any.whl`, SHA-256 `1324e51892e62e3c9eea327d73c3bb0232521faade1966cda68014ee3a63e08f` |
| SDK wheel | `integral_sdk-0.2.0-py3-none-any.whl`, SHA-256 `332594454623815532e826bd4aa5c66b08ebb91cc97027e6fea33aa027cf20ad` |
| Signed Asset Register archive | `asset-register-1.0.0.tar.gz`, SHA-256 `64fd82b5adc6d47716aad054ceb6405ea493ab7107cd09729ab4a249dfc093f1`; signing public-key SHA-256 `fc3a816521fd733a772a0996bbe8efc7532b1731f3b5f027708bc22843ddcb07` |
| API image | `ghcr.io/v75inc/integral-core-qualification-api@sha256:484a6fbe716b93a9c062cc40518b56b0920399a60fa7dda2ade2f078d7cd9bc6` |
| Web image | `ghcr.io/v75inc/integral-core-qualification-web@sha256:eeb693353b1fc8d13a37e72950f4dbb00e1eb6d1e3705eba13f89078f14bf781` |
| Registry qualification | [Run 36973609020](https://github.com/V75inc/integral-core/actions/runs/36973609020); exact SHA, immutable digests, clean pull, fresh Postgres and `/data` volumes, Chromium passed |
| Configuration | Registry deployment used `INTEGRAL_CORE_ONLY=1` and supplied no global model-provider keys |
| Wheel/archive build environment | macOS arm64, CPython 3.11.15, `SOURCE_DATE_EPOCH=1790920630`; signed archive public key above |

The wheel and signed archive hashes above were built from this exact source
revision. CI's independent-artifact lane passed on the same revision. The
registry workflow built and deployed the exact SHA and retained the image
digests and browser report in its artifacts.

### Candidate-specific row disposition

| Row | Result on `e408246` | Evidence and remaining boundary |
| --- | --- | --- |
| A01 | **PASS** | Registry Chromium signup, generic App/Track/Entry creation, saved table View creation and reopen. Run 36973609020 `browser-smoke.json`. |
| A02 | **PASS** | Same provider-free Core-only registry journey; no global model-provider keys. Run 36973609020. |
| A03 | **PASS** | Module-boundary guard passed in CI on this exact source SHA. Run 36972185880. |
| A04 | **PASS** | Exact-digest probes: foreign scope create 403; owner create 200; revoked private reads/list and Entry create denied; public Track readable/read-only; resident/MCP denied writes not persisted. Run 36973609020 `browser-smoke.json`. |
| A05 | **FAIL** | New helper returns one resolved projection copied across four surface labels; it does not exercise the actual form, view, dashboard, and agent-query implementations. `backend/tests/contracts/test_c6_row_proofs.py::test_a05_rename_null_and_collision_agree_on_every_surface`. |
| A06 | **FAIL** | Postgres test proves injected crash rollback and one retry effect, but omits concurrent commands and a single effect/receipt assertion across the concurrent-crash sequence. `backend/tests/contract/test_operation_execution_receipts_postgres.py::test_a06_crash_then_retry_commits_one_effect_and_one_receipt`. |
| A07 | **FAIL** | In-memory approval token test does not execute a corrected approved revision with a durable effect/receipt or prove continuation/recovery. `backend/tests/contracts/test_c6_row_proofs.py::test_a07_approval_executes_once_and_reports_correction_expiry_cancel`. |
| A08 | **FAIL** | In-memory ledger deduplication is not a process restart, persisted progress, or materialization proof. `backend/tests/contracts/test_c6_row_proofs.py::test_a08_restart_resumes_the_same_requirement_ledger`. |
| A09 | **PASS** | Existing backend and frontend parity suites passed in CI on this source SHA; the added projection helper is not used by those surfaces. Run 36972185880 and [parity details](2026-09-21-a09-query-view-parity.md). |
| A10 | **FAIL** | Synthetic impact data and a pure guard call do not execute a populated schema alteration, rollback, or rejection against stored records. `backend/tests/contracts/test_c6_row_proofs.py::test_a10_unmigrated_populated_change_keeps_records`. |
| A11 | **FAIL** | Decision-helper assertions do not exercise a persisted unknown external outcome, provider observation, or worker reconciliation/retry. `backend/tests/contracts/test_c6_row_proofs.py::test_a11_unknown_external_outcome_reconciles_before_retry`. |
| A12 | **FAIL** | Source query parity and generic browser reads do not prove one Asset Register UI/HTTP/resident/MCP operation with the same receipt and no duplicate effect on these registry digests. |
| A13 | **FAIL** | Separate upgrade preview and pause/resume tests omit a populated upgrade with outstanding-work fencing across restart and uninstall. |
| A14 | **FAIL** | The retained populated restore used a local image. Registry run 36973609020 did not restore a dump plus `/data` archive using its exact image digests and verify authenticated attachment download hash. |
| A15 | **FAIL** | 531 relative targets/anchors were checked, but external URLs were skipped, `make -n verify-ci` only dry-ran, no documentation command was executed, and no independent author trial is recorded. `scripts/c6_active_doc_check.py`; independent trial remains required. |
| A16 | **OUTSIDE CORE** | External live-model qualification is separate and non-blocking for this platform matrix. |

### Registry browser evidence

The browser report records no browser errors, the saved View reopening, the
two-user A04 scope/revocation results, resident and MCP write rejection, no
denied Entries in the owner's inventory, and a generic Entry read through
HTTP, resident, and MCP. The run created fresh named Postgres and file volumes
and clean-pulled the two recorded image digests. It did not perform the A14
populated restore drill or the Asset Register A12 operation/receipt trial.

## Historical candidate: `7965594`

| Artifact | Identity |
| --- | --- |
| Git source | `7965594aafccca23d945e40663d254dd693c54e2` |
| Core wheel | `integral_core-0.1.1rc11-py3-none-any.whl`, SHA-256 `3365f76e315a52ed89a637ea805b2ac521c4c5cd786840952f1f86d1f3109b37` |
| SDK wheel | `integral_sdk-0.2.0-py3-none-any.whl`, SHA-256 `bb0577e7e77a07ca444c947bf95ad886ce7c6c9819b98727249ae8995c3c3a48` |
| Signed Asset Register archive | `asset-register-1.0.0.tar.gz`, SHA-256 `0089e88cfd2329423a0ecd9a0b45156c87de05bbe003f79873f79a0c60672989`; signing public-key file SHA-256 `66f6e9b21103e52e8559446ec11a5579763ac62194a64bb6fa59cd553f2659e9` |
| API image | `ghcr.io/v75inc/integral-core-qualification-api@sha256:4065dd3a993051e8a2bd6f910102f84d1b5f07b232e8bfda58d132506456e5a7` |
| Web image | `ghcr.io/v75inc/integral-core-qualification-web@sha256:f1c110a9576ab8853842388b4bfa418860da50d4a679890ddc231db4d97c9ff2` |

`make verify-independent-artifacts` passed on main. The Core wheel was
reproducible across its repeated build (`3365f76e…`). The recorded signed
Asset Register archive was built from this tree; the independent gate also
verified an external Asset Register archive after extraction and loaded it
outside the checkout.
The qualification was run through [registry workflow 36945949667](https://github.com/V75inc/integral-core/actions/runs/36945949667)
at the exact candidate SHA. Its API and web image builds, immutable digest
recording, clean-runner digest pulls, deployment, and Chromium step all ran
and passed; Docker verification was not skipped. The deployment used newly
created Postgres and file volumes, `INTEGRAL_CORE_ONLY=1`, and no global model
provider keys.

## Candidate-specific row disposition

**FAIL** means the row's stated acceptance condition is not fully demonstrated
for this SHA; it does not assert that the implementation necessarily has a
defect. Partial and skipped evidence is not promoted to a pass.

| Row | Result on `7965594` | Evidence and remaining boundary |
| --- | --- | --- |
| A01 | **FAIL** | Fresh registry deployment completed signup and generic App/Track/Entry creation. A blank Dashboard was additionally created through the visible UI on the local `main` build. A saved generic `View` has not been created and verified on the exact registry images. |
| A02 | **PASS** | The registry deployment ran with `INTEGRAL_CORE_ONLY=1` and no global provider keys; Chromium signed up and created an App, Track, and Entry, then opened that Entry from global Tracks. No browser errors were recorded. |
| A03 | **PASS** | `.ci/module_boundary_check.sh` passed on exact main source (`module-boundary: OK`). |
| A04 | **FAIL** | Exact registry-image two-user probe passed foreign Track create refusal (403), owner control (200, correct Workspace), revoked private direct reads (403), Mission Control omission, revoked Entry create (403), and public read-only control (200/403). An extended probe passed on a local build from the same source SHA: denied Track absent from both requester-owned inventories; revoked member Track list denied (403); resident and MCP Entry writes rejected; authenticated owner inventory remained unchanged. These added effect checks are not yet proven on the immutable registry digests, so A04 remains failed. |
| A05 | **FAIL** | No candidate-specific fixture proves rename, null, and platform/Business collision behavior across shared form, view, dashboard, and agent query. |
| A06 | **FAIL** | No candidate-specific fresh-Postgres concurrent command plus injected-crash proof with one effect and one receipt. |
| A07 | **FAIL** | No candidate-specific approval, correction, expiry, cancellation, and continuation/recovery receipt trace. |
| A08 | **FAIL** | No candidate-specific restart/resume run proving no duplicate objects and a complete requirement ledger. |
| A09 | **PASS** | Re-run on this tree: `backend/tests/contracts/test_a09_query_view_parity.py` passed (2 tests); `frontend/src/components/views/__tests__/A09ViewParity.test.tsx` passed (4 tests). |
| A10 | **FAIL** | No populated schema alteration and rollback/rejection proof on this candidate. |
| A11 | **FAIL** | No external unknown-outcome reconciliation/retry proof on this candidate. |
| A12 | **FAIL** | Local Asset Register contract tests passed, and the Postgres-only one-receipt replay test was included in the fresh Postgres lane. No single published-image trial proves the same Asset Register operation and query across UI, HTTP, resident, and MCP with one receipt and no duplicate effect. |
| A13 | **FAIL** | No populated upgrade, pause, restart, and uninstall capability-fencing drill on this candidate. |
| A14 | **FAIL** | Registry deployment used fresh empty Postgres and file volumes, but no populated dump plus `/data` file archive was restored into scratch database/volume on these exact images and followed by authenticated hash-matching download. |
| A15 | **FAIL** | The previous local-link scan is historical. This candidate has no retained all-doc heading-anchor and external-link result, executable-command trial, or independent author's trial. |
| A16 | **OUTSIDE CORE** | External live-model qualification stays separate from Core and does not block this platform matrix. |

The candidate's fresh Postgres lane completed successfully against an
isolated Postgres volume with two worker databases (`pytest -q --tb=short -n
2 --dist loadfile -m 'not domain_app and not slow'`). Explicit skips included
pgvector driver checks (extension unavailable), benchmark tests, Atlas tests
(no external Atlas URI), and tests requiring unseeded optional library
packages. `make verify` completed successfully, including all 215 frontend test files
(1,287 tests), backend suite, type/lint/build checks, and CI-faithful smoke.
The separate fresh-Postgres lane also passed. The runner reported explicit
skips for unavailable pgvector/Atlas services, benchmarks, Postgres-only cases
in the non-Postgres lane, and unseeded optional library packages. Index-based
pre-commit guards were vacuous because this worktree's changes were unstaged;
the non-index guards passed. These engineering gates do not convert the failed
acceptance rows above into passes.

The extended exact-SHA local browser/API evidence is retained at
`/tmp/c6-main-evidence-a04-rerun/browser-smoke.json`; it reports foreign
scope denial, owner create success, requester inventory absence, former-member
private list/read denial, resident and MCP write rejection, no denied entries
persisted, and public read-only behavior. Its `local-main-built` image labels
mean it supports development diagnosis only; do not treat it as a registry
digest qualification. The updated helper script SHA-256 is
`b420cc0df6b31412ad275b73a222eb0f1f4bf046c46bc494ef546ea528e1b069`.

## File-volume transition

The existing Core API data volume previously mounted at `/app/integral_data`
now mounts at `/data`; new attachment files live at `/data/files`. An operator
moving an existing root-owned volume must stop the API and perform the
documented one-time ownership repair before attachment writes. The fresh
registry file-volume probe passed, but it does not repair or qualify an
operator's pre-existing volume. See [deployment storage and recovery guidance](../../ops/DEPLOY.md#other).

## Historical da33c68 run (superseded; not candidate evidence)

The records below preserve the prior run for comparison only. None of its
artifact hashes, image digests, A04 result, restore result, or selected
resident journey is carried into the frozen-candidate row dispositions.

## Root cause and repair

An attachment upload against the deployed API returned 500. The API ran as
UID 10001, but its default `/app/.files` path was not writable. The Compose
volume mounted `/app/integral_data`, which did not contain those files. A
failed upload also left a detached Attachment node because the node was
created before file storage initialization. The one disposable orphan from
that attempt was removed after diagnosis.

The runtime now sets `JVSPATIAL_FILES_ROOT_PATH=/data/files`, creates that
directory with UID 10001 ownership, and mounts the persistent Compose volume
at `/data`. Entry, chat, and chunked attachment uploads initialize storage
before creating an Attachment node. The image qualification workflow mounts
a fresh file volume, writes through the exact API image, and reads the file
back from a separate container.

## Candidate identity and gates

| Artifact | Identity |
| --- | --- |
| Core wheel | `integral_core-0.1.1rc11-py3-none-any.whl`, SHA-256 `1b01987bd561c3a5f111c1f9b233924b384f47c131aab60a6e6936db79dd25b2` |
| SDK wheel | `integral_sdk-0.2.0-py3-none-any.whl`, SHA-256 `b32a763405825d2fcf081485930ace54e8cbf663366abefbb8e7d89bb013444f` |
| Signed, extracted Asset Register archive | SHA-256 `f49cdac85266f02be9f3a2f2e65517cec519ddad1564763934be741f6bff0fcc` |
| Local API image | `sha256:9ea9030ba824b73e6d2ab8686428611fafd212986303ed4f31391580840f3810`, labeled with the source revision |
| GHCR API image | `ghcr.io/v75inc/integral-core-qualification-api@sha256:1b013390e32786b5d5969955b14cef4a08b75a304f894cee0eead369794bd031` |
| GHCR web image | `ghcr.io/v75inc/integral-core-qualification-web@sha256:9d48e5024997bea85402e5dbe1f87c2b8407c1caf5136cc97890ac71c9cbf625` |

`make verify` passed on the staged source, including pre-commit guards,
format/lint, types, CI-faithful smoke, the frontend suite, and the full
backend suite. Fresh-worker Postgres `make test-postgres` passed. Core-only,
contract, clean-install, SDK artifact, and external Asset Register artifact
gates passed. Focused attachment tests covered entry, chat, and chunked
storage initialization. PR #97 backend, frontend, independent-artifact, and
Postgres CI checks passed on this revision; the ordinary Docker CI job was
skipped. The separate [registry qualification run 36802954970](https://github.com/V75inc/integral-core/actions/runs/36802954970)
passed publication and clean digest-pull deployment on a fresh runner with
separate Postgres and file volumes. Its file probe survived a separate
container readback.

## Populated backup and independent restore

On the exact local image, authenticated upload of a 60-byte synthetic file
created `n.Attachment.24693fd76a3248fc901d7359`, structurally attached
to entry `n.Entry.475a5791e2b84b27a67fc085`. Authenticated download and
the stored file each matched SHA-256
`2f489fa034d095d573323e0c4c8c5819c25ede11083fcc2066d7f38d5a21f878`.
The API was stopped for a consistent PostgreSQL dump and file-volume tar:

| Backup | SHA-256 |
| --- | --- |
| `da33-populated-with-attachment.dump` | `c61b79aeb105588fb3aa7246e2c8c6b743d17285d00b2ac58988dce1de0e80d1` |
| `da33-files.tar` | `7fa28201214a6a3ae3b28934405d379a309b3aca7a313472e87217e486933b01` |

Those backups were restored into a separate scratch database and file volume.
A new API container from the same image and revision became healthy. Its
authenticated attachment download matched the original byte hash. Its
governed HTTP and MCP Asset Register queries returned the same nine rows,
including the resident-created asset; unauthenticated MCP returned 401.
Replaying the original resident operation through HTTP and MCP returned the
same durable receipt and entry. This is a selected populated restore drill,
not proof of every A14 fixture or recovery mode. Local backup files and
readback logs are retained under `/private/tmp/integral-c6-d497-artifacts/`
and `.qualification-evidence/da33-full-restore-readback.log`.

## Exact-candidate resident, browser, and transport journey

The signed-in browser used synthetic workspace
`n.Workspace.939d23f26bcd4dcdbfaed02e` and installed App
`n.WorkspaceApp.434da202d8e3424999c69460`. GPT-4.1 was asked to register
tag `C6-DA33-001`, title `C6 da33 Resident Asset`, category
`it_equipment`. The first `integral_invoke_app_operation` call lacked an
idempotency key and returned `bad_request` without an effect. It corrected
the call in the same turn and succeeded through the declared `register_asset`
operation in 8.2 seconds. Run ID:
`d8bf83fd-9457-4575-9140-471570cc7a48`; durable receipt:
`o.OperationExecutionReceipt.965c5301e99e32d5ab4113b252347601009b151a17cfcc68c0b3eac4b9d64863`;
new entry: `n.Entry.8c510dad93924e18b28b987e`. The Track Feed visibly
showed ten entries with the new title, tag, and category. Track and view
**Improve this** controls were icon-only with accessible names and tooltips.

After that write, authenticated HTTP and MCP `available_assets` queries
returned the same ten rows, object references, and applied workspace scope;
the new asset was present. Unauthenticated MCP returned 401. HTTP and MCP
replay of idempotency key `c6-da33-001-register` each returned
`replayed=true`, the original receipt, and the same entry without a second
effect. The browser write required one corrected tool call; it was not a
first-attempt success.

## Qualification boundary

### A04 live scope failure found after the selected journeys

On 2026-10-01 UTC, the exact da33 API was probed through the port-19100
same-origin proxy with two newly registered synthetic users and separate
organization workspaces. The owner could list its own workspace (200). The
other user received 403 when listing that workspace and 400 for a bare,
noncanonical scope header. However, that same other user sent
`POST /api/tracks` with `X-Integral-Scope` naming the owner's workspace and
no `workspace_id` body field; the API returned 200. The owner's track count
remained zero. This is a **failed A04 effect-boundary outcome**, regardless
of whether the new track landed in the caller's default workspace. Source
inspection confirms the create route passed only the optional body
`workspace_id` to `create_track_in_space` and did not validate the supplied
header. The MCP probe in that first script stopped at a 307 redirect and
is not counted as a transport result. A successor code candidate must reject
the bad header before any write, then rerun the cross-surface matrix.

### A03 module boundary guard

On 2026-10-01, `.ci/module_boundary_check.sh` was run against this source
tree. Its reviewed allowlist permits `app.contracts`, `app.modules`,
`app.schemas`, and the single legacy service `app.services.policy_engine`.
The guard rejected a temporary `backend/app/modules/` module importing
`app.models` with exit 1 and `forbidden module import app.models`; after the
probe was removed, the same guard passed with exit 0 and
`module-boundary: OK`. The guard is included in `make guards` and the
pre-commit hook. This proves the tested forbidden import fails the local
build guard on the candidate; it does not replace review of every module
boundary or the broader C6 matrix.

### A15 local documentation links

On 2026-10-01, a local-target scan covered 57 Markdown files: the docs hub
and current product, backend, operations, and Operational Model references.
It resolved 483 relative link targets and found none missing after replacing
five checkout-specific source links, two removed App-manifest references, and
links to companion repositories that cannot be assumed present in a Core
checkout. One literal `/api/attachments/{id}/download` example was excluded
as an API route, not a file link. This scan checks target existence, not
heading anchors, external URLs, command execution, document coherence, or
independent author trials. A15 therefore remains partial.

### Ordinary Core without a global model provider

On 2026-10-01, the same local da33 API container reported no
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GOOGLE_API_KEY` environment value,
and `INTEGRAL_CORE_ONLY=1`. Its image revision label and digest matched the
candidate identity above. The d497 web image served the unchanged frontend
bytes through `http://127.0.0.1:19100`; its `/api/` reverse proxy reached
the da33 API container.

In that browser deployment, a new synthetic user signed up, skipped optional
email verification, and opened the empty Mission Control and Apps surfaces.
The user created blank App `n.WorkspaceApp.73a0b3af436f4b90a1f1a9ab`,
default-model Track `n.Track.64f3e65e389645cf82bedb23`, and Post
`n.Entry.1f4081da3af8498ebaf59ab3`. The Track Feed showed the new title
and body. After a page navigation/reload, the same entry was still present.
The user signed out and signed in again; the entry remained visible. An
authenticated `GET /api/entries/n.Entry.1f4081da3af8498ebaf59ab3` through
the same-origin proxy, with the active `X-Integral-Scope: ws:…` header,
returned 200 and the stored title and body. This is a passing selected A02
ordinary-use journey without a global provider; it does not exercise an
agent turn or prove every A02/A01 journey on a fresh installation.

The host also had a separate Python server bound to IPv4
`127.0.0.1:4000`, while Docker published the qualification API on port 4000
through a different listener. Direct host IPv4 requests to that port reached
the separate server and rejected this synthetic account. Those 401 responses
are not attributed to the da33 container or counted as an A02 failure. The
browser and successful API readback used port 19100 and its verified proxy.

The local browser used the exact da33 API image and the prior d497 web image;
da33 did not change frontend source. The separate registry runner built both
images from da33 and supplied digest-pull deployment evidence. The
broader A01–A15 denial, lifecycle, concurrency, recovery, and human journey
matrix has not been fully reconciled on this source revision. The older
`bb3b1e0` table is historical. Independent architecture and Product Owner
decisions remain pending. C6 is not complete, and no production release is
asserted.
