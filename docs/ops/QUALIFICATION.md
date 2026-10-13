# Capability and release qualification

This guide describes how to qualify a Core change; it is not a live release-status page. The dated records below preserve the exact scope, revision, and limits of earlier checks. Their results do not qualify today's source or artifact.

A source feature, green CI, a built wheel, a healthy container, and an accepted user journey are separate facts. Record evidence for the exact artifact and configuration being promoted.

## Qualification domains

| Area | Qualification focus |
|---|---|---|
| Knowledge | Scope, traversal, cascade, restore on the selected adapter |
| Collaboration | Workspace/resource roles, direct grants, exclusions, shares, revocation, public-read exceptions, cross-workspace denial |
| Modeling | Authoring/runtime schema compatibility, drafts, existing-record impact, interrupted migration, failed-item retry |
| Apps | Actual dependencies, trust policy, pause, upgrade, uninstall, active-definition behavior |
| Experience | Browser readback agrees with authorized query and record results |
| Resident AI | Actual providers, BYOK modes, approvals, tool outcomes, interruption and recovery |
| Durable work | Production PostgreSQL contracts, lease/fencing behavior, external-effect reconciliation |
| Work mandates | Public admission and execution boundaries; do not infer runnable support from a reviewed contract |
| Retrieval | Scope prefilters and backend-specific semantics; do not infer universal vector search from pgvector installation |
| Connectors | Each actual service, credential flow, revocation and failure behavior |
| Attachment screening | Configured adapter behavior; a no-op is not malware screening |
| Usage | Physical observations and source-aware reconciliation; estimates or unavailable cost do not establish definitive billing |

## Source gates

Run `make verify` for the broad local gate and `make verify-ci` for CI-faithful smoke testing. CI uses TESTING, no developer `.env`, and xdist. Run `make verify-core-only` to establish the Core/App boundary.

Run relevant PostgreSQL contracts with a disposable database and temporary test encryption keys. Preserve existing development databases. A green test against an in-memory adapter does not establish transaction/CAS behavior on PostgreSQL.

## Artifact gates

Build a clean wheel, inspect its contents, install it into a clean environment, initialize a blank distribution, and run its packaged UI. Check pinned dependency resolution and explicit index provenance. Package publication requires its own release authorization and readback.

## User-flow gates

Test authentication, workspace switching, record create/read/update, typed relation/member/file presentation, comments, sharing and revocation, views, App Home, dashboards, resident answers, staged application, and result readback. Include a second principal and a second workspace.

For enabled durable paths, interrupt a worker before and after effect boundaries. Verify lease loss, event replay, current-grant rechecks, uncertain-effect reconciliation, and final product transcripts. Confirm that canceling in the UI reaches the backend rather than merely hiding a stream.

## Local launcher and desktop preview — 8 October 2026

The launcher was exercised locally on macOS Apple Silicon with a private pgserver 0.1.4 PostgreSQL database, real authentication, `DEBUG=false`, and active rate limiting. Checks covered signup, authenticated reads, App persistence, stop/restart, stable API/web addresses, concurrent startup, backup/restore of keys and file bytes, paths containing spaces, and switching to an independently installed Core wheel. The public lifecycle descriptor and conditional shutdown keep native-client ownership separate from an existing or replacement runtime.

Integral Business owns the transferred Electron client. An unsigned local macOS application built from the current Core wheel reached the login screen, accepted a test-account sign-in, retained its session on restart, and shut down its owned runtime on quit. Desktop tests include the transferred broker/host modules; those optional native capabilities remain disabled until their obsolete backend APIs are ported through a reviewed Business integration contract.

The backend rerun completed with 4,951 passed and 282 skipped tests; the frontend suite passed 1,566 tests, and the desktop suite passed 51 tests. The README onboarding check initially failed after the documentation rewrite and passed after package/container/private-secret guidance was restored. These checks do not establish provider, BYOK, durable process-interruption, cross-platform installer, signing or publication qualification. Physical local backups require the matching operating system, architecture and PostgreSQL version.

## Promotion record

Record commit and artifact identity, lockfile, non-secret settings, database adapter, provider routes, test commands/results, browser journeys, remaining defects, and rollback/restore method. Do not copy credentials into the record. Release approval should address this concrete evidence.

The roadmap's wider ambitions are not substitutes for these checks. See [deployment](DEPLOY.md) and [security](SECURITY.md).

## Dated qualification records

These records are historical evidence, not current package or deployment status:

- [Core foundation and resident harness — 9 October](qualification/2026-10-09-core-foundation.md)
- [Native harness — 9 October](qualification/2026-10-09-native-harness.md)
- [Managed environment isolation — 9 October](qualification/2026-10-09-managed-environment-isolation.md)
- [Business model configuration — 9 October](qualification/2026-10-09-business-model-configuration.md)
- [Build field identity — 9 October](qualification/2026-10-09-build-field-identity.md)
- [Core 0.1.1rc16 release preflight — 9 October](qualification/2026-10-09-core-rc16-preflight.md)
- [UI polish — 10–12 October](qualification/ui-polish/2026-10-10.md)
- [Core rc18 and published JV Spatial 0.2.2 — 13 October](qualification/core-rc18/2026-10-13.md)
- [Database operation logging — 12 October](qualification/db-logging/2026-10-12.md)
- [Harness recovery — 12 October](qualification/harness-recovery/2026-10-12.md)
- [Access-control review and qualification](qualification/access-control-review/README.md)
- [Workspace ontology context qualification](qualification/ontology-context/FINAL-ACCEPTANCE.md)

## Native harness reliability review, 9 October 2026

The [native harness qualification record](qualification/2026-10-09-native-harness.md) documents reproduced HTTP crypto failures and redundant approval continuation, setup key preservation, canonical PostgreSQL entry commands, and managed-installation backup/restore. Read its final gate results and limits alongside the earlier snapshot above.
