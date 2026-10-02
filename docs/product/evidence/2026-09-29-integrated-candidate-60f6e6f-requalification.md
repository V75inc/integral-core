# Integrated candidate requalification — 2026-09-29

## Candidate identity

- **Source revision:** `60f6e6fa4c22181ba17bba7b7e2d6001678cafc8` on local branch `codex/local-integration-candidate`.
- **Parent:** `bbd55db6335e5956c643debcc9c04179e9699612`, with PR #88's `1a8ccce54403643f66ef51e041d694b4e746307e` local Ollama credential support cherry-picked.
- **Scope:** dependency-ordered local integration stack through PR #89, PR #90's restore fix, and PR #88's local Ollama support. This candidate is 106 commits ahead of `main`, not pushed, not merged, and not a release candidate.

## Exact-candidate evidence

| Check | Result | Boundary |
| --- | --- | --- |
| Repository gate | **Pass** — `make verify` exited 0. It completed pinned formatting, Python lint and mypy, frontend ESLint (394 warnings, zero errors), TypeScript, wheel verification, CI-faithful backend smoke, all 213 frontend files / 1,273 tests, and the full backend suite. | Exact source SHA. The `make verify` guard step noted the current index was empty; to cover that gap, the exact diff from `abe1ced` to this SHA was applied in a disposable main-based worktree, staged there, and all 16 substrate guards passed. |
| CI-faithful Postgres | **Pass** — `make test-postgres-ci` passed with `JVSPATIAL_POSTGRES_DSN=postgresql://integral:integral@localhost:5433/postgres`. | Exact source SHA; local PostgreSQL 16.14 test service. The first `make verify-pr` attempt used its default `localhost:5432` endpoint and failed fixture bootstrap; the same CI Postgres target then passed against the configured test service. |
| Full Postgres backend | **Pass** — `make test-postgres` exited 0 with two workers. | Exact source SHA; PostgreSQL 16.14. Atlas, benchmark, pgvector-connectivity, unseeded library, and explicitly deferred integration cases were skipped under their declared conditions. |
| Independent artifacts | **Pass** — `make verify-independent-artifacts` built and loaded the Core wheel, installed it in a clean environment, imported the SDK wheel, and installed and loaded the independent Asset Register archive. Retained exact-candidate artifact hashes are recorded below. | Exact source SHA. Retained binaries establish artifact identity; the ledger still is not a frozen release-candidate ledger. |
| Ollama | **Pass, existing-account candidate browser flow** — on the exact source SHA, the existing authenticated `W5.2 QA Recheck` browser session selected `Ollama (Local)` / `gemma4:e2b`; the UI's local-connection check succeeded; the selection saved and remained active after a full page reload; and a chat prompt returned a visible local response labeled `gemma4:e2b` (20.5s). The candidate API ran in local DEBUG mode on port 4003 against the local PostgreSQL QA database; `OLLAMA_API_BASE` pointed to loopback Ollama. No OpenAI key or Ollama API key was passed to the API, and Ollama reported the local `gemma4:e2b` model loaded. PR #88's hosted backend, frontend, Postgres, and independent-artifact checks also pass. | This proves one configured existing-account browser chat journey on the candidate. It is not the full C6 browser acceptance matrix or the W0.3b held-out exam. |
| Dashboard modal padding | **Pass, focused UI contract and visual check** — `AppDashboardPanel.dialog.test.tsx` passed on this candidate and asserted `Modal.Body` uses the standard `px-5 sm:px-6 py-5 space-y-4` gutters. In the live exact-candidate browser, the chart/dashboard results dialog visibly had clear horizontal and vertical gutters around its title, recomputed result, rows, and footer action. | Exact-SHA focused test and browser-visible populated drill-through; the full C6 browser matrix remains open. |
| Dashboard browser drill-through | **Pass, positive open-class journey; packaged-App path denied by policy** — in the exact candidate container images, a workspace-authored App with two synthetic records displayed count 2; the results modal loaded both rows and recomputed count 2. The signed Asset Register package's generic dashboard widget displayed the explicit `This App requires a declared query capability` denial. | Browser-visible proof is exact-source and on a fresh disposable SQLite database. It qualifies the open-class path only; it does not establish packaged-App declared-query dashboard support, PostgreSQL operation dispatch, cross-App focus isolation, or full C6 acceptance. See [App-focus and dashboard recheck](2026-09-29-60f6e6f-app-focus-dashboard-recheck.md). |
| W0.3b custody validator | **Correctly refuses live run** — `validate_qualification_contract.py --require-recorded` reports `corpus is pending Q custody; live qualification refused`. | The split manifest still has no external corpus locator, version, digest, provider/configuration pin, scoped credential reference, or approved budget. No held-out scenario was run. |

## Retained candidate artifact identities

These files were rebuilt from the exact source revision above and retained
outside Git under `/private/tmp/integral-core-candidate-artifacts-60f6/`.
Their SHA-256 values are:

| Artifact | SHA-256 |
| --- | --- |
| `integral_core-0.1.1rc11-py3-none-any.whl` | `d5a6dfdc3d44cb365551aec87ceda788a12be18b919c2d1671020bb651647134` |
| `integral_sdk-0.2.0-py3-none-any.whl` | `bc0178fc67a98d4a30b7f12cc72e431b3f2dbe464e44820ee223ae3022452fab` |
| `asset-register-1.0.0.tar.gz` | `741314806006cb8ef4e021429c7a995c651ed65203a8d847f0f26864115cec81` |
| Asset Register verification public-key file | `375eeaccad129f543913f1556021d81d0f033f0fc76e24c189835683f4620da7` |

Core verified the retained signed archive using its normal signature loader
(`verified=True`, `reason=valid`). The separately mounted Asset Register
package used for browser smoke has payload digest
`32bd3f57b73f1f1df455450504b29f554e553e811b0ee4656faa5ac44bd56e56`; the
candidate API verified its mounted signature against its configured public
key (`verified=True`, `reason=valid`). This distinguishes the retained
archive from the separately signed package mounted in the browser container.

The browser API and web images have local Docker image IDs
`sha256:6da410eb64527319d7eb17069fc93ac76c8c3165893ad8a95168c0058e59e008`
and
`sha256:d2366180ce6e490f31a550277486e307f862dc3605e98412958e09dfd909fa73`,
respectively. Neither image has a registry RepoDigest; these local image IDs
are not registry-published OCI digests. Earlier retained Core and SDK wheel
files have different whole-archive hashes, while their ZIP member names and
payload bytes match these rebuilt wheels; wheel archive metadata is not
reproducible in the current build environment.

## Qualification boundary and remaining work

This candidate has exact-SHA source, CI-faithful smoke, Postgres, frontend, backend, Core and SDK wheel hashes, an independent App archive hash and signature verification, local API/web image IDs, and an existing-account browser Ollama chat journey with reload-persistence evidence. `make verify-pr` initially exited non-zero because its default CI Postgres DSN did not match the available local test service; the corrected target passed, and `make verify` plus the full Postgres suite passed independently. The exact candidate still lacks registry image digests, the full C6 browser acceptance matrix, exact-SHA transport-parity journeys, and Product Owner architecture/release review.

W5.2 remains incomplete: the local account's attempt to install the migrated CRM App was correctly denied because it lacks the commercial entitlement; the available tracked Business manifests declare no query capabilities, so they cannot prove populated-App dashboard values or governed drill-through. No Business repository changes were made. Human recommendation acceptance remains open. W0.3b remains blocked on Q's external custody and budget record. The live local Ollama smoke does not substitute for that corpus or exam. The candidate and open PRs remain separate from `main`; release approval and publication remain separate Product Owner decisions.
