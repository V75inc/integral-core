# Restore-fix requalification — 2026-09-29

## Candidate identity

- **Source revision:** `bbd55db6335e5956c643debcc9c04179e9699612` on local branch `codex/local-integration-candidate`.
- **Parent candidate:** `530b53768733bfdc2282ff88f215fd74ac2985dc`.
- **Scope:** applied PR #90's lazy-Object-table restore fix to the dependency-ordered local integration candidate. The source tree still contains the open package stack through PR #89 and is not merged to `main` or a release candidate.

## Exact-candidate evidence

| Check | Result | Boundary |
| --- | --- | --- |
| Focused Postgres restore contract | **Pass** — `test_backup_restore_drill_round_trip` passed against the exact candidate. The test initialized the graph tables, created a dump, restored it to a scratch database, matched row counts with the absent Object table interpreted as zero, matched identity fingerprints, and cleaned up the scratch database. | Exact source SHA; focused integration test. |
| Full Postgres backend | **Pass** — `make test-postgres` exited 0 against the local disposable Postgres service, with two xdist workers. | Exact source SHA; Atlas, benchmark, pgvector-connectivity, and unseeded library cases skipped under their documented gates. |
| `make verify-pr` | **Partial** — guards, pre-commit hooks, format/lint/type checks, Core-only and extension-contract lanes, CI-faithful backend smoke, and all 208 frontend files / 1,254 tests passed. The final CI-faithful Postgres subtarget failed during fixture bootstrap: its default `localhost:5432` / `integral:integral` credentials did not match the running service, and pytest then reported the Python 3.14 event-loop setup error. | The full Postgres backend lane passed separately on the configured local service at port 5433. |
| PR #90 hosted CI | **Pass** — backend, frontend, Postgres, and independent Core/SDK/App artifact jobs passed on PR #90's `main`-based branch. | This validates the source PR against `main`; it is not a substitute for rerunning every CI job on this integrated stack. |

## Qualification boundary

This closes the exact-candidate direct-restore gap for `bbd55db` and confirms the full Postgres suite on that tree. It does not freeze a release candidate: the integrated package PRs remain open and unmerged, the CI-faithful Postgres job was not run successfully against this local integrated tree, independent SDK/App artifact hashes were not retained for this SHA, and the full browser acceptance matrix remains incomplete. W5.2 declared-query and human recommendation acceptance and W0.3b's Q-custodied live-model exam remain open. Product Owner review and release approval remain separate gates.
