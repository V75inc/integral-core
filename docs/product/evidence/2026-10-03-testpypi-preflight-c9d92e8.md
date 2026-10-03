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

The local frontend build used Node `23.10.0`, which emits unsupported-engine warnings for packages requiring Node 20/22/24. The GitHub release workflows pin Node 20. The build completed locally; the exact Node 20 workflow build still needs to run on GitHub after the workflow fix merges. Build output also reported an existing large JavaScript chunk warning and two ambiguous Tailwind utilities.

## Fresh deployment and browser smoke

A separate Compose project (`integral-final-smoke`) was built from the merged source and started with isolated fresh PostgreSQL and file volumes. The deployment remains running for review.

- Web: `http://127.0.0.1:19006` — HTTP 200.
- API readiness: `http://127.0.0.1:14002/api/health/ready` — HTTP 200.
- API and web containers report healthy; the database is isolated to this smoke project.
- On the production-built web UI: created a synthetic account and organization workspace; switched between personal and organization workspaces; created a Track, Entry, and saved Table view; confirmed the row rendered; visited Apps, Feed, and Settings/Agent; checked light and dark themes; and confirmed the workspace switch did not show an error page.
- Sent a short real model request in the fresh app; it returned the requested exact response. Provider credentials came from local ignored environment configuration and are not recorded here.

The compose logs included jvspatial warnings about PostgreSQL index identifiers exceeding PostgreSQL's 63-character limit for action-model indexes. Startup and readiness succeeded. This should be investigated separately before claiming warning-free database migrations.

## Current handoff

The checked-out `main` was advanced to the merged SHA, then a local branch was created for the workflow, changelog, release-guide, and acceptance-ledger updates. No application code was changed by this preflight. The version remains `0.1.1rc11`, no release was uploaded, and no push was made. After the workflow fix is merged, require successful exact-SHA CI and a successful TestPyPI workflow before deciding whether to bump the next candidate version.
