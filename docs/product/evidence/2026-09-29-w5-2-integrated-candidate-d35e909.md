# W5.2 integrated candidate qualification — 2026-09-29

## Candidate identity

- **Source revision:** `d35e909c6d3fbd1f7062c8c2aec0c744cc5e3b8b` on local branch `codex/c6-w52-integrated`.
- **Parent:** `60f6e6fa4c22181ba17bba7b7e2d6001678cafc8`.
- **Scope:** the integrated candidate plus PR #92's declared App query aggregate implementation. This is a local qualification candidate; it is not pushed, merged to `main`, or a release candidate. PR #92 remains the reviewable implementation PR.

## Exact-candidate evidence

| Check | Result | Boundary |
| --- | --- | --- |
| `make verify` | **Pass** — repository gate completed, including guards, formatting/lint/type checks, backend smoke and full backend suite, and frontend tests. | Exact source SHA; the combined candidate was qualified before commit. |
| Full PostgreSQL backend suite | **Pass** — `make test-postgres` exited 0 with two workers. | Exact source SHA; declared Atlas, benchmark, pgvector connectivity, unseeded library, and integration skips remain outside this evidence. |
| CI-faithful PostgreSQL lane | **Pass** — `make test-postgres-ci` exited 0 against the configured local PostgreSQL service. | Exact source SHA; this records the local CI-faithful target, not a GitHub Actions run. |
| Focused query/dashboard tests | **Pass** — 166 backend tests, 9 dashboard widget registry frontend tests, and TypeScript checking. | Exact source SHA. |
| Independent artifacts | **Pass** — `make verify-independent-artifacts` built and loaded the Core wheel, installed Core into a clean environment, imported the SDK wheel, and loaded the independent signed Asset Register archive. | Exact source SHA. Core wheel SHA-256 from this run: `19ede48d5c69b92b7398bc1db51749e3dce621fdbcd59c82d8480c3217837cff`. This is a local build identity, not a published registry artifact. |
| PostgreSQL browser dashboard read | **Pass, aggregate value only** — the exact-candidate API and candidate SDK artifact served the declared Asset Register query against the isolated PostgreSQL browser database. The authenticated dashboard visibly displayed `Available assets: 3`. | The browser frontend came from PR #92's worktree, not from this integrated source tree. This proves the positive aggregate read for this fixture; it does not establish drill-through, migrated-App parity, or full W5.2 acceptance. |

The initial browser query attempt returned `declared_query_failed` because the isolated candidate API environment did not have the separately published `integral_sdk` wheel installed. Installing the exact SDK artifact resolved the environment mismatch; the rerun returned the visible aggregate value without a source-code change. The API used the local PostgreSQL test database. No model-provider request was involved.

## Remaining W5.2 acceptance

W5.2 remains **incomplete**. The browser fixture contains three synthetic typed Asset records in the independent Asset Register. It does not cover populated migrated Business Apps with declared queries, the complete populated-query corpus, human acceptance of recommendation quality, or a successful packaged-App drill-through journey on this candidate. The result must not be generalized beyond the tested query and fixture.
