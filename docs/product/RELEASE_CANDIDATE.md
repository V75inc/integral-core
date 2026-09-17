# Release candidate evidence — Foundation public developer sprint

**Status:** Locally verified release candidate (not published)
**Branch:** `feat/foundation-public-developer-sprint`
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
| Contract lane | `make verify-contract` | Pass (15 tests) |
| Core-only lane | `make verify-core-only` | Run before publish |
| Artifact import | `.ci/verify_artifact_baseline.sh` | Pass |
| Frontend unit | `npm run test:run` (extension host) | Pass |

## AC coverage snapshot

| AC | Status | Evidence |
| --- | --- | --- |
| AC-01 | Partial | Artifact script; full smoke in WP-08 |
| AC-02 | Partial | `test_asset_register_manifest.py` |
| AC-03 | Partial | View host + hello_panel prototype |
| AC-04 | Partial | Operations dispatcher; MCP binding TBD |
| AC-05 | Partial | Postgres spike; custody integration TBD |
| AC-06 | Partial | Policy gate in dispatcher |
| AC-07–12 | Incomplete | WP-04/08 integration pending |
| AC-13 | Partial | `docs/developer/quickstart.md` |
| AC-14 | Partial | This document |

## Known limitations (developer preview)

- Trusted Python execution requires deployment operator configuration.
- Iframe view bridge exposes a minimal read surface.
- `ToolContext.get_employee_compensation` retained with deprecation path.
- Asset Register `create_entry` awaits full ToolContext write surface.
- Postgres concurrency proofs require `INTEGRAL_TEST_DB=postgres`.

## Human acceptance

Pending Eldon review per sprint §12.
