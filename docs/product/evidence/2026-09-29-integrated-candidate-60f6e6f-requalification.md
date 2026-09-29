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
| Independent artifacts | **Pass** — `make verify-independent-artifacts` built and loaded the Core wheel, installed it in a clean environment, imported the SDK wheel, and installed and loaded the independent Asset Register archive. The retained Core wheel SHA-256 was `3e97734217b72371a168370a5939744e0e299a21fe98458c89843bcdffb53711`. | Exact source SHA. SDK and App archive builds passed, but their hashes and signing-key identity were not retained, so the release ledger remains incomplete. |
| Ollama | **Pass, local inference only** — `ollama run gemma4:e2b` returned a response with the locally installed model. No OpenAI call or cloud key was used. PR #88's hosted backend, frontend, Postgres, and independent-artifact checks also pass. | Confirms the local model is runnable. It does not prove a configured Integral chat journey or the W0.3b exam. |
| Dashboard modal padding | **Pass, focused UI contract** — `AppDashboardPanel.dialog.test.tsx` passed on this candidate and asserted `Modal.Body` uses the standard `px-5 sm:px-6 py-5 space-y-4` gutters. The dashboard modal and shared Modal source are unchanged from the earlier `530b537` browser recheck, which visually confirmed chart-modal gutters. | Focused test on exact SHA; visual browser check is from `530b537`, not a full browser run on this candidate. |
| W0.3b custody validator | **Correctly refuses live run** — `validate_qualification_contract.py --require-recorded` reports `corpus is pending Q custody; live qualification refused`. | The split manifest still has no external corpus locator, version, digest, provider/configuration pin, scoped credential reference, or approved budget. No held-out scenario was run. |

## Qualification boundary and remaining work

This candidate has exact-SHA source, CI-faithful smoke, Postgres, frontend, backend, Core wheel, clean-install, SDK-load, and independent-App-load evidence. `make verify-pr` initially exited non-zero because its default CI Postgres DSN did not match the available local test service; the corrected target passed, and `make verify` plus the full Postgres suite passed independently. The exact candidate still lacks retained SDK/App artifact hashes and container image digests, a fresh full browser acceptance matrix, exact-SHA transport-parity journeys, and Product Owner architecture/release review.

W5.2 remains incomplete: the local account's attempt to install the migrated CRM App was correctly denied because it lacks the commercial entitlement; the available tracked Business manifests declare no query capabilities, so they cannot prove populated-App dashboard values or governed drill-through. No Business repository changes were made. Human recommendation acceptance remains open. W0.3b remains blocked on Q's external custody and budget record. The live local Ollama smoke does not substitute for that corpus or exam. The candidate and open PRs remain separate from `main`; release approval and publication remain separate Product Owner decisions.
