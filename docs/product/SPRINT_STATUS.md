# Sprint status — Foundation public developer

**Branch:** `feat/foundation-public-developer-sprint`
**Updated:** 2026-09-17

| WP | Status | Notes |
| --- | --- | --- |
| WP-00 | **Done** | ADR-011, AC map, postgres spike, view prototype, ops contract test |
| WP-01 | **Partial** | `.ci/verify_artifact_baseline.sh`; full CI Postgres lane TBD |
| WP-02 | **Done** | Operations dispatcher, idempotency Object, `/api/extensions/.../operations`, SDK stub |
| WP-03 | **Done** | AppExtensionViewHost, bridge, static serve, hello_panel prototype |
| WP-04 | **Partial** | Lifecycle hooks register/unregister ops; trust reconciliation TBD |
| WP-05 | **Done** | `examples/asset-register` manifest + tools + contract test |
| WP-06 | **Partial** | Declarative views in manifest; custom asset detail view TBD |
| WP-07 | **Partial** | Skills + schedule declared; runtime schedule proof TBD |
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
