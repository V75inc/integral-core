# TestPyPI preflight — 2026-10-03

**Source main revision:** `c9d92e8139d759b3ba0b4d661708a6cad2175370` (PR #106 merge)
**Package version:** `0.1.1rc11` (already published; no version bump or upload in this preflight)
**Disposition:** Local product and artifact checks pass. The exact-main CI run passed. Both release workflows were triggered on that SHA and failed before checkout because `gh run list` had no repository context. A local workflow fix and documentation updates are prepared on `codex/testpypi-preflight-main-fixes`; they still need to be merged and exercised by GitHub Actions before the next version bump.

## Source and GitHub evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Main synchronization | **PASS** — fetched and fast-forwarded local `main` to the PR #106 merge SHA; clean base before the preflight edits | `c9d92e8139d759b3ba0b4d661708a6cad2175370` |
| CI on exact main SHA | **PASS** | [Run 37121476119](https://github.com/V75inc/integral-core/actions/runs/37121476119) |
| TestPyPI workflow on exact main SHA | **FAIL before checkout/build** — `gh run list` could not determine the base repo because `workflow_run` starts without a Git checkout | [Run 37121877791](https://github.com/V75inc/integral-core/actions/runs/37121877791) |
| PyPI workflow on exact main SHA | **FAIL before checkout/build** — same missing repository context | [Run 37121877747](https://github.com/V75inc/integral-core/actions/runs/37121877747) |
| Corrected CI lookup | **PASS locally** — both workflow scripts now pass `--repo "$REPOSITORY"`; the exact-SHA `gh run list` query returned the successful CI run | `.github/workflows/publish-testpypi.yml`, `.github/workflows/publish-pypi.yml` in the prepared branch |

The workflow correction is a release blocker until merged and observed passing on GitHub. No TestPyPI publication was attempted. The next release candidate must use the next unused version after checking TestPyPI; the published `0.1.1rc11` cannot be overwritten.

## Local release gates

All gates ran against the merged source tree; the package-only evidence below was built after running the same web and resident-harness bundling scripts used by the release workflow.

| Gate | Result |
| --- | --- |
| `make verify` | **PASS** — guards, format/lint/types, CI-faithful backend smoke, frontend suite (218 files / 1,298 tests), and full backend suite |
| `make verify-pr` | **PASS** — includes Core-only boundary, extension-contract lane, frontend suite, and CI-faithful PostgreSQL tests using an isolated disposable PostgreSQL 16 service |
| `make verify-independent-artifacts` | **PASS** — reproducible Core wheel, clean install/import, SDK wheel, and signed external App artifact |
| `make audit` | **PASS** — no unreviewed known vulnerabilities; existing reviewed dependency disposition remains documented |
| Release asset scripts | **PASS** — `.ci/bundle_web_assets.sh` and `.ci/bundle_resident_harness.sh` completed |
| Built distribution | **PASS** — Core `0.1.1rc11` wheel and sdist built; `twine check` passed both; installed-wheel import passed |
| Wheel asset inspection | **PASS** — wheel contains `app/web/static/index.html`, `app/resident_harness/app.yaml`, and the Integral resident agent manifest |
| Built wheel SHA-256 | `d06cf5055805fc974e8fe980e62471f81210160f6d092f74780f596adaf2e2b3` |

At the initial preflight, the frontend build used Node `23.10.0`, which emitted unsupported-engine warnings, a deprecated Vite chunking option, ambiguous Tailwind utilities, and a large initial bundle. Those findings were addressed in the follow-up changes recorded below.

## Fresh deployment and browser smoke

A separate Compose project (`integral-final-smoke`) was built from the merged source and started with isolated fresh PostgreSQL and file volumes. The deployment remains running for review.

- Web: `http://127.0.0.1:19006` — HTTP 200.
- API readiness: `http://127.0.0.1:14002/api/health/ready` — HTTP 200.
- API and web containers report healthy; the database is isolated to this smoke project.
- On the production-built web UI: created a synthetic account and organization workspace; switched between personal and organization workspaces; created a Track, Entry, and saved Table view; confirmed the row rendered; visited Apps, Feed, and Settings/Agent; checked light and dark themes; and confirmed the workspace switch did not show an error page.
- Sent a short real model request in the fresh app; it returned the requested exact response. Provider credentials came from local ignored environment configuration and are not recorded here.

The compose logs included jvspatial warnings about PostgreSQL index identifiers exceeding PostgreSQL's 63-character limit for action-model indexes. The warning was reproduced and traced to generated names in the pinned `jvspatial==0.1.0` dependency. The deterministic shortening, concurrency-safe schema bootstrap, updated contract, and PostgreSQL regression tests are in [jvspatial PR #51](https://github.com/TrueSelph/jvspatial/pull/51). Its full coverage-enabled suite and pre-commit checks pass against PostgreSQL 16. Core remains on the published pin until an upstream fixed release is available and qualified.

## Current handoff

The checked-out `main` was advanced to the merged SHA, then a local branch was created for the workflow, application, changelog, release-guide, and acceptance-ledger updates. The version remains `0.1.1rc11`; no release was uploaded. Require successful exact-SHA CI and a successful TestPyPI workflow after merge, and qualify the upstream PostgreSQL identifier fix before claiming warning-free database setup or deciding on the next candidate version.

## Follow-up remediation

- Release workflow repository lookup corrected and locally verified against the exact source SHA; a GitHub Actions run is still required after merge.
- Node 20.20.2 production build completed after replacing the deprecated Vite `advancedChunks` option with `codeSplitting`, removing the ambiguous Tailwind utilities, and respecting reduced-motion preferences in the connector animation.
- View manifests now load implementation modules on demand while retaining their metadata eagerly. The frontend typecheck and Vitest suite pass (218 files / 1,298 tests). Production build output is recorded with the final PR validation.
- The PostgreSQL index-name issue and a concurrently discovered first-use table bootstrap race are fixed in [jvspatial PR #51](https://github.com/TrueSelph/jvspatial/pull/51), with real PostgreSQL regression coverage. Until that upstream change is released and Core updates its dependency pin, the current Core build can still emit the dependency's index-name warnings.
- The release branch's `make verify-pr` run passes guards, formatting/type checks, Core-only and extension-contract lanes, and frontend tests. Its first run could not connect to the documented default PostgreSQL DSN because that local service was stopped; rerunning the full gate with the running isolated PostgreSQL service on port 15434 completes the database lane.
- The Node 20 build now has a 352.65 kB (101.87 kB gzip) application entry, down from approximately 1.2 MB (350 kB gzip) at preflight. Large chat and rich-text vendor chunks remain route-loaded and independently cached.
- Rebuilt the running local smoke deployment from the revised production frontend. The SPA and API readiness endpoints returned HTTP 200; a browser session loaded the existing smoke Track and its saved Table view in light mode, including the on-demand view chunk and rendered entry row.
