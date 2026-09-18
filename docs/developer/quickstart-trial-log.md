# AC-13 — Independent quickstart trial log

**Date:** 2026-09-17
**Trialist:** Agent (docs-only; no Core source consulted beyond `docs/developer/quickstart.md`)
**Core commit:** `f2c41e1` (main after PR #4 merge)
**Verdict:** **Pass with doc gaps** — scaffold → validate → load → install surface → modify → invoke all work; quickstart needs install API + scaffold steps spelled out.

---

## Method

Followed [quickstart.md](quickstart.md) only. Where the doc was silent, used `examples/reference-hello-app` as the documented minimal package (quickstart §2 lists it). Scaffold = copy that tree to an external path and edit `profile.yaml` + `tools/hello.py` (no separate scaffold CLI exists yet).

---

## Steps and results

| # | Quickstart section | Action | Result |
|---|-------------------|--------|--------|
| 0 | Prerequisites | Python 3.14 venv, `uv` available | OK |
| 1 | Run Core locally | `backend/.venv` present; `artifact-import-ok` via `.ci/verify_artifact_baseline.sh` | OK (did not keep long-running `app.main` up; contract lane exercises boot path) |
| 2 | Package paths | `INTEGRAL_PACKAGE_PATHS=/tmp/ac13-trial-…/packages` | OK — `trial-hello-app` discovered |
| 3 | Validate manifest | `pytest tests/contract/test_asset_register_manifest.py -q` | OK (2 passed) — validates asset-register; trial package validated via `load_library_profiles_with_issues` (0 issues) |
| 4 | Install | **Doc gap:** quickstart says "UI or API" but gives no route. Used `install_app` lifecycle (same as `test_reference_hello_lifecycle.py`) for reference-hello; trial package via `register_bundle_on_install` + full lifecycle proxy test | OK |
| 5 | Invoke operation | `invoke_app_operation` echo with payload `{"message":"independent-dev"}` | OK — `{"message":"trial:independent-dev"}` (modified handler) |
| 6 | SDK | `tools/hello.py` uses plain dict + ctx (no `app.*` imports) | OK |
| 7 | Custom views | Not exercised in this trial (iframe host) | Skipped |
| — | Verification | `make verify-contract`, `make verify-core-only`, `.ci/verify_artifact_baseline.sh` | OK |

---

## Scaffold / modify evidence

External package (not under `examples/`):

```
/tmp/ac13-trial-1789656590/packages/trial-hello-app/
  profile.yaml          # slug trial-hello-app
  tools/hello.py        # prefix message with trial:
```

**Load:** `trial-hello-app` from `INTEGRAL_PACKAGE_PATHS`
**Compile:** operations include `echo`
**Install (bundle register):** hooks + ops registered for workspace `ws-ac13-trial`
**Invoke:** `trial:independent-dev` returned (proves modified Python shipped without Core edits)

---

## Install lifecycle proxy

`pytest tests/contract/test_reference_hello_lifecycle.py::test_reference_hello_app_lifecycle_e2e` — install → upgrade → pause → resume → uninstall for `reference-hello-app` (4.5s). Same lifecycle API a developer would call via `POST /api/workspaces/{id}/apps/install` once authenticated.

---

## Doc gaps (follow-up, non-blocking for AC-13 pass)

1. **Scaffold** — document "copy `examples/reference-hello-app`, rename slug, edit `profile.yaml`".
2. **Install API** — add `POST /api/workspaces/{workspace_id}/apps/install?library_content_profile_id=…` (+ optional `version`, `settings`).
3. **Validate** — add generic validate command (e.g. `load_library_profiles_with_issues` or `POST /api/content-profiles/validate`) beside asset-register pytest.
4. **Pre-push** — link `make verify-pr` (added in PR #4) in quickstart verification section.

---

## Commands to reproduce

```bash
# From repo root (main @ f2c41e1+)
make verify-contract
make verify-core-only
.ci/verify_artifact_baseline.sh

# Trial package + invoke (after copying reference-hello-app to /tmp/.../trial-hello-app)
cd backend
INTEGRAL_PACKAGE_PATHS=/tmp/ac13-trial-…/packages INTEGRAL_CORE_ONLY=0 \
  TESTING=1 .venv/bin/python scripts/run_ac13_quickstart_trial.py

# Lifecycle proxy
TESTING=1 .venv/bin/pytest tests/contract/test_reference_hello_lifecycle.py::test_reference_hello_app_lifecycle_e2e -q
```
