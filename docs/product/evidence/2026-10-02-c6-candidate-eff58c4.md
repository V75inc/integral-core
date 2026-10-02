# C6 candidate qualification — 2026-10-02

**Candidate source:** `eff58c4d6d42c4a01c85177485a08c5e403ee64d`
**Disposition:** **C6 is not complete.** All technical rows A01–A14 pass on this
candidate. A15's independent author trial is still outstanding and is recorded
as a fail until a reviewer completes it with retained findings. A16 remains
outside Core. The new Architecture review and Product Owner decision are
pending; publication remains separate.

## Candidate identity and qualification

| Artifact / gate | Identity or result |
| --- | --- |
| Git source | `eff58c4d6d42c4a01c85177485a08c5e403ee64d` |
| Core wheel | `integral_core-0.1.1rc11-py3-none-any.whl`, SHA-256 `fb79f08ae5b6de398c1214cfe8a424a3acaf32167788e1f87413499990696d6c` |
| SDK wheel | `integral_sdk-0.2.0-py3-none-any.whl`, SHA-256 `acaec5fca4c126e52a93ea41473ca9c4284dc6545714d5a329254741b7dffa10` |
| Signed Asset Register archive | `asset-register-1.0.0.tar.gz`, SHA-256 `f0d6aea27acffe0f43e6cff39eb3056067b54f30d8023146152a631c869607fd` |
| API image | `ghcr.io/v75inc/integral-core-qualification-api@sha256:ccaa386f5bb45538e1602bc610e1294a1831f2c533d31504ac370ea207a8b824` |
| Web image | `ghcr.io/v75inc/integral-core-qualification-web@sha256:21e5688783c1e5144c3f07401a8bf07575e86f3c1135e8a5ae80aacb748015fd` |
| Registry qualification | [Run 37013122424](https://github.com/V75inc/integral-core/actions/runs/37013122424), all three jobs passed; images built and pulled by digest with Docker verification enabled |
| Registry configuration | `INTEGRAL_CORE_ONLY=1` for the Core browser and restore run; no provider credentials injected; fresh PostgreSQL and file volumes mounted at `/data`; A12 used fresh PostgreSQL/file volumes with the signed external App |
| Local source gate | **PASS** — `make verify`: 16 repository guards, pinned formatting/lint/types, reproducible Core wheel, CI-faithful smoke, 216 frontend files / 1,291 tests, and full backend suite. Optional external-service and Postgres-only tests are handled by separate qualification below. |
| Independent artifact gate | **PASS** — `make verify-independent-artifacts`: reproducible Core wheel, clean Core install, SDK wheel import, and extracted signed external Asset Register load |
| Fresh-Postgres C6 contracts | **PASS** — 8/8 targeted tests: A06 crash/retry and concurrent waiter; A07 corrected approval; A08 process restart/resume; A10 populated-schema rejection; A11 unknown-outcome reconciliation; A12 single durable Asset operation across transports; A13 populated upgrade and work fencing across restart/uninstall. The A05 source contract passed in the full suite. |
| Documentation audit | 111 Markdown files, 536 relative targets, no missing links or anchors; all 17 external URLs returned success to HTTP HEAD (GET Range fallback used for 403/405). |

## Candidate-specific row disposition

| Row | Result on `eff58c4` | Evidence |
| --- | --- | --- |
| A01 | **PASS** | On the exact registry images, Chromium completed first signup/login and created a generic App, Track, Entry, and saved View; reopening the View succeeded. Retained in the registry deployment artifact. |
| A02 | **PASS** | Provider-free journey ran with `INTEGRAL_CORE_ONLY=1`; registry configuration injected no provider credentials. |
| A03 | **PASS** | Exact candidate `make verify` guards passed, including module-boundary checks. |
| A04 | **PASS** | Two-user browser/API probe: foreign scoped Track create 403 with no persistence; owner create 200; revoked user loses private Track read/list and Entry create; public Track remains readable and read-only. Resident and MCP denied writes persisted no Entry. |
| A05 | **PASS** | Cross-surface rename/null/collision contract passed in the full source suite on the candidate tree. |
| A06 | **PASS** | Fresh-Postgres crash/retry and concurrent-waiter tests each retained one effect and one durable receipt. |
| A07 | **PASS** | Fresh-Postgres corrected approved revision executed once and terminal approval states closed accurately. |
| A08 | **PASS** | Fresh-Postgres separate-process restart/resume retained progress and did not duplicate materialization. |
| A09 | **PASS** | Full exact-tree backend and frontend source suites passed; optional services are listed as skipped, not counted as passes. |
| A10 | **PASS** | Fresh-Postgres populated-schema rejection occurred before mutation. |
| A11 | **PASS** | Fresh-Postgres unknown outbox outcome was reconciled before retry; no duplicate delivery. |
| A12 | **PASS** | Signed external App installed through Manage Apps on fresh Postgres/file volumes. UI, HTTP, resident, and MCP returned the same operation receipt `o.OperationExecutionReceipt.9c92fce1b5b9f3fdc6ef1e6adbea94e4e2da98c9cedf01da3740c63930e9619d` and the same single Asset `n.Entry.9d4de726fe484a899d41f89b`; first UI call was original, other transports replayed it. All four queries returned exactly that one Asset. |
| A13 | **PASS** | Fresh-Postgres populated App upgrade preserved tenant state and fenced due work across restart and uninstall. |
| A14 | **PASS** | Exact API digest restored a populated database dump and `/data` archive into scratch Postgres/volume. Authenticated download SHA-256 matched the stored attachment: `1be7e9c8deddf0de6a94eea30f7ff12c8bcf4929d7994b16eef4113b1a1280e2` (15 bytes). Dump SHA-256 `973a1ea9586e2d22ddacffbef998b55c587e115b84da57a9ee82439b2da16f80`; file archive SHA-256 `480e015a24cbcb7e60f83fc12691ba0e5a883c19d28793d208cf9190857db15f`. |
| A15 | **FAIL — independent author trial outstanding** | Internal links/anchors passed (111 files / 536 targets); all 17 external URLs returned success; executable `make verify`, `make verify-independent-artifacts`, and the documented fresh-Postgres C6 command passed. Only the independent author trial with retained findings remains. |
| A16 | **OUTSIDE CORE** | External live-model qualification remains a separate product decision. |

## Data-volume migration note

Existing volumes mounted at `/app/integral_data` now mount at `/data`; new
attachments are written under `/data/files`. Before attaching an old volume,
operators must perform the one-time ownership repair described in the
[deployment guide](../../ops/DEPLOY.md). The C6 restore evidence uses the new
mount and proves database-plus-file-volume recovery.

## Human review still required

1. An independent documentation author must run the A15 checklist against this
   packet and retain their findings. Link and command checks are already done;
   the reviewer should independently reproduce them and record any differences.
2. Request a new independent Architecture review against SHA
   `eff58c4d6d42c4a01c85177485a08c5e403ee64d` and this evidence packet. Earlier
   reviews rejected different candidate packets and do not carry forward.
3. Record the Product Owner's new decision against this exact candidate.
4. Keep publication as a separate decision after acceptance.

The signed registry/browser evidence is retained in
[GitHub Actions run 37013122424](https://github.com/V75inc/integral-core/actions/runs/37013122424).
