# Acceptance test map — Foundation public developer sprint

Maps AC-01..14 to automated tests and manual evidence. Update as WPs land.

| AC | Observable result | Automated test(s) | WP |
| --- | --- | --- | --- |
| AC-01 | Clean Core boots, signup, generic views/API | `test_smoke_*`, artifact harness (WP-01) | WP-01, WP-08 |
| AC-02 | Independent Asset Register install, no Core edits | `tests/contract/test_asset_register_app.py` | WP-05 |
| AC-03 | Package views in native nav; tenant isolation | `test_asset_register_extension_view.py`, browser | WP-03, WP-06 |
| AC-04 | UI/HTTP/agent/MCP same operation impl | `test_app_operations_shared_semantics.py` | WP-02, WP-07 |
| AC-05 | Concurrent checkout: one success, one conflict; idempotent retry | `test_custody_concurrency_postgres.py` (`@pytest.mark.postgres`) | WP-05, WP-08 |
| AC-06 | Denied reads/writes fail closed | `test_app_operations_policy.py` | WP-02, WP-08 |
| AC-07 | Upgrade preserves customizations; migration recovery | `test_asset_register_upgrade.py` | WP-04, WP-05 |
| AC-08 | Pause/uninstall suppresses routes/tools/views/schedules | `test_reference_hello_lifecycle.py` (extend) | WP-04 |
| AC-09 | Warranty schedule durable + deduped | `test_warranty_schedule.py` | WP-07 |
| AC-10 | Audit trail checkout → custody | `test_custody_audit.py` | WP-05, WP-08 |
| AC-11 | Tampered artifact rejected pre-execution | `test_package_trust.py`, `test_asset_register_trust.py` | WP-04 |
| AC-12 | Backup restore integrity | `test_restore_rehearsal.py` (manual + scripted) | WP-08 |
| AC-13 | Public quickstart trial | `docs/developer/quickstart.md` + `quickstart-trial-log.md` | WP-09 |
| AC-14 | Release gated on exact artifact digest | `publish-pypi.yml` and `publish-testpypi.yml` verify `INTEGRAL_WHEEL_PATH`, then the publish job rejects a different SHA-256 | WP-10 |

## Wave 0 spikes

| Spike | Test file | Pass criteria |
| --- | --- | --- |
| Conditional state transition | `tests/spikes/test_conditional_state_transition.py` | Two concurrent `find_one_and_update`; exactly one succeeds on Postgres |
| Operation invoke (hello) | `tests/contract/test_app_operations_hello.py` | `POST /extensions/{app}/operations/echo` returns tool output |
| View host prototype | `frontend/src/extensions/__tests__/AppExtensionViewHost.test.tsx` | Handshake + one bridge read message |

## Shared fixtures

- `examples/reference-hello-app` — minimal F0/F2 contract (operations + tools)
- `examples/asset-register` — full domain proof (WP-05+)
- Postgres: `INTEGRAL_TEST_DB=postgres` / sandbox `:5435`
