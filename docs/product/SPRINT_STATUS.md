# Sprint status — Foundation public developer

**Branch:** `feat/sprint-wave2-3`
**Updated:** 2026-09-17

| WP | Status | Notes |
| --- | --- | --- |
| WP-00 | **Done** | ADR-011, AC map, postgres spike, view prototype, ops contract test |
| WP-01 | **Done** | Artifact baseline + CI `test-postgres` (spikes + contract postgres lane) |
| WP-02 | **Done** | Operations dispatcher, idempotency Object, `/api/extensions/.../operations`, SDK stub |
| WP-03 | **Done** | AppExtensionViewHost, bridge (read + operation), static serve, hello_panel prototype |
| WP-04 | **Done** | Trust tier + signature gate (`tools/` python), pause lifecycle, upgrade preserves settings |
| WP-05 | **Done** | `examples/asset-register` manifest + tools + contract test |
| WP-06 | **Done** | `asset_detail` extension view + operation bridge from host |
| WP-07 | **Done** | Schedule materialize + pause gate + `run_scheduler_pass` dedupe proof |
| WP-08 | **Partial** | Policy deny, custody audit/conflict, postgres primitive; restore rehearsal TBD |
| WP-09 | **Partial** | `docs/developer/quickstart.md`; independent trial TBD |
| WP-10 | **Partial** | `RELEASE_CANDIDATE.md` updated; Eldon review pending |

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
See `RELEASE_CANDIDATE.md` for AC coverage gaps (restore, MCP semantics, trial).
