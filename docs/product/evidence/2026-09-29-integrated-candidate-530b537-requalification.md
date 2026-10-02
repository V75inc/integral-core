# Integrated candidate requalification — 2026-09-29

## Candidate identity

- **Source revision:** `530b53768733bfdc2282ff88f215fd74ac2985dc` on local branch `codex/local-integration-candidate`.
- **Base:** `origin/main` at `abe1cedf480dc3a7a2861d8c28746f36074807ea`.
- **Scope:** local dependency-ordered integration of the open package stack, plus the dashboard denial-feedback follow-up from PR #89. The branch is 104 commits ahead of `origin/main`; it is not merged to `main` and is not a release candidate.
- **GitHub state:** PRs #70–#89 were open when checked on 2026-09-29. PR #89 is stacked on #81 and had no GitHub checks reported at that time. The exact state is separate from this local candidate's gates.

## Automated qualification

Commands ran from the exact source tree above. Full command output is in the local ignored `.qualification-evidence/` directory in the candidate worktree.

| Gate | Result | Evidence |
| --- | --- | --- |
| Repository | **Pass** — `make verify`; 213 frontend files and 1,272 tests passed; the full non-Postgres backend suite passed. | `.qualification-evidence/2026-09-29-candidate-530b537-repository.log` |
| PR CI reproduction | **Pass** — `make verify-pr`, including guards, formatting/type checks, Core-only, extension-contract, CI-faithful backend, frontend, and the CI-faithful Postgres markers. | `.qualification-evidence/2026-09-29-candidate-530b537-verify-pr.log` |
| Full Postgres backend | **Pass** — `make test-postgres` against a new, disposable Postgres 16.14 service with two xdist workers; process exit code 0. Atlas, benchmark, pgvector-connectivity, and unseeded-library cases were skipped under their documented gates. | `.qualification-evidence/2026-09-29-candidate-530b537-postgres-suite-final.log` and `.exit` |
| Independent artifacts | **Pass** — Core wheel import, clean-install ASGI import, SDK wheel import, and extracted Asset Register load. Core wheel `integral_core-0.1.1rc11-py3-none-any.whl` SHA-256: `961de42fe7ccca95c2c3fc646e4d1abbc50a37664be21d7442bee60cc94405d9`. The temporary SDK wheel and App archive were not retained, so their hashes are not recorded for this candidate. | `.qualification-evidence/2026-09-29-candidate-530b537-independent-artifacts.log` |

The Postgres service used image digest `sha256:fa3d9bb7ee77f5c1f0bfb009a9df30243c040896825f3033b09a77101bb2ca95`, PostgreSQL 16.14, and no persistent volume. That container was stopped after the run. The independent artifact lane's temporary outputs were cleaned by its runner.

## Exact-candidate browser check

The Vite frontend and API processes both ran from `/private/tmp/integral-core-integration` at the candidate revision. The API was connected to the local PostgreSQL database `integral_w52`; the browser used its existing signed-in `W5.2 QA Recheck` workspace and migrated Guyana Payroll fixture.

- Opened the `Total Base salary` dashboard contributing-record dialog.
- Visually confirmed horizontal and vertical padding around the title and body.
- The governed read returned `This App requires a declared query capability`; the UI showed that API reason instead of a raw Axios error. The request remained denied because the packaged App has no declared query capability.
- Integral Business containers were present on the host, but the browser frontend and backend under test were the Core candidate worktree. No Integral Business files were changed.

A second isolated browser origin (`localhost:19008`) used the same candidate Vite frontend and PostgreSQL-backed API without disturbing the existing QA session. A synthetic account signed up, skipped optional email verification, and saw a clean personal workspace with zero Tracks. It created `Core First Track QA 530b`; after a full page reload the workspace still showed exactly one Track with that name. This rechecks the first-Track persistence journey on the `530b537` source candidate and Postgres-backed API. It does not repeat W6.3's effective-skills catalogue or the synthetic dashboard journey.

In the same fresh account, Settings → AI Skills loaded the workspace-focused effective catalogue: **16 skills and 122 tools**. This confirms the basic W6.3 workspace catalogue render on `530b537`; app-focus filtering, install/revocation transitions, and the synthetic dashboard journey were not repeated here.

This is focused dashboard evidence. The complete fresh-account, first-Track, effective-skills, and synthetic-dashboard browser journey was recorded on the preceding candidate `3da6199ff4aa1c0405562f5d9f9d348e7b504786`; that run is not represented here as a 530b537 browser pass. The populated 102-row drill-through fixture is separately recorded in [W5.4 package evidence](https://github.com/V75inc/integral-core/blob/main/docs/product/evidence/packages/W5.4.yaml).

## Qualification boundary

The automated lanes, focused dashboard denial/padding browser check, fresh-account first-Track persistence, and 16-skill/122-tool workspace catalogue render passed for `530b537`. C6/WP-09 remains **incomplete**: the direct restore drill, app-focus catalogue cases, and synthetic dashboard journey have not been repeated on this exact SHA; SDK and App artifact hashes were not retained; transport-parity evidence is still partial; Product Owner review is pending. The W0.3b live-model exam remains outside Core and incomplete pending Q custody of the held-out corpus, pinned provider/model configuration, and approved budgets. This record does not declare a release or authorize publication.
