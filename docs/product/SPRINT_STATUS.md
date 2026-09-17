# Sprint status — Foundation public developer

**Branch:** `feat/sprint-wave2-3`
**Updated:** 2026-09-17

| WP | Status | Notes |
| --- | --- | --- |
| WP-00 | **Done** | ADR-011, AC map, postgres spike, view prototype, ops contract test |
| WP-01 | **Partial** | Artifact baseline + CI `test-postgres` spike lane; full AC-05 concurrency TBD |
| WP-02 | **Done** | Operations dispatcher, idempotency Object, `/api/extensions/.../operations`, SDK stub |
| WP-03 | **Done** | AppExtensionViewHost, bridge (read + operation), static serve, hello_panel prototype |
| WP-04 | **Partial** | Ops trust gate + paused-app reject; migration recovery TBD |
| WP-05 | **Done** | `examples/asset-register` manifest + tools + contract test |
| WP-06 | **Done** | `asset_detail` extension view + operation bridge from host |
| WP-07 | **Partial** | Skills + `default_schedules` → RoutineTask in uplink_registry; E2E proof TBD |
| WP-08 | **Pending** | Adversarial integration + restore rehearsal |
| WP-09 | **Partial** | `docs/developer/quickstart.md` |
| WP-10 | **Partial** | `RELEASE_CANDIDATE.md` evidence snapshot |

## Wave checkpoints

### Wave 0 — complete
- [x] ADR-011
- [x] AC → test map
- [x] Postgres conditional-update spike
- [x] Operations invoke contract test
- [x] View host prototype
- [ ] Eldon architecture review

### Wave 1 — complete
- [x] External package builds/loads (hello + asset-register)
- [x] Typed operation invoke (echo)
- [x] Custom view mount (hello_panel)

### Waves 2–5 — in progress
See `RELEASE_CANDIDATE.md` for AC coverage gaps.
