# Release candidate evidence — Foundation public developer sprint

**Status:** Locally verified release candidate (not published)
**Branch:** `feat/sprint-wave2-3`
**Date:** 2026-09-17

## Artifact identity

| Component | Location | Notes |
| --- | --- | --- |
| Core backend | `integral-core/backend` | Apache-2.0 |
| Core frontend | `integral-core/frontend` | Bundled with Core |
| Public SDK | `sdk/python/integral_sdk/` | Typing/helpers only |
| Reference Hello | `examples/reference-hello-app` | Minimal contract proof |
| Asset Register | `examples/asset-register` | Domain proof package |

Record exact commit SHA and wheel digest at publication time.

## Gates executed (local)

| Gate | Command | Result |
| --- | --- | --- |
| Contract lane | `pytest tests/contract/ -m contract` | Pass (30 tests, 1 skipped postgres-only) |
| Core-only lane | `make verify-core-only` | Run before publish |
| Artifact import | `.ci/verify_artifact_baseline.sh` | Pass |
| Postgres lane | `INTEGRAL_TEST_DB=postgres pytest -m "contract and postgres"` | CI job |

## AC coverage snapshot

| AC | Status | Evidence |
| --- | --- | --- |
| AC-01 | Partial | Artifact script; full smoke in WP-08 |
| AC-02 | Done | `test_asset_register_app.py` |
| AC-03 | Done | `test_asset_register_extension_view.py` + hello_panel |
| AC-04 | Partial | Operations dispatcher; MCP shared-semantics TBD |
| AC-05 | Partial | Postgres spike + `test_custody_concurrency_postgres.py`; live custody race TBD |
| AC-06 | Done | `test_app_operations_policy.py` |
| AC-07 | Done | `test_asset_register_upgrade.py` |
| AC-08 | Done | `test_reference_hello_lifecycle.py` (hooks + ops + schedules) |
| AC-09 | Done | `test_warranty_schedule.py` (materialize + pause + scheduler dedupe) |
| AC-10 | Done | `test_custody_audit.py` |
| AC-11 | Done | `test_package_trust.py` |
| AC-12 | Pending | `test_restore_rehearsal.py` |
| AC-13 | Partial | `docs/developer/quickstart.md` |
| AC-14 | Partial | This document |

## Known limitations (developer preview)

- Trusted Python execution requires deployment operator configuration (`INTEGRAL_PROFILE_PUBKEY`).
- Signature gate now covers `tools/**/*.py` bundles (e.g. asset-register).
- Iframe view bridge exposes read + operation invoke.
- Asset Register `create_entry` awaits full ToolContext write surface in some paths.
- Postgres custody concurrency end-to-end not yet wired to atomic `find_one_and_update`.

## Human acceptance

Pending Eldon review per sprint §12.
