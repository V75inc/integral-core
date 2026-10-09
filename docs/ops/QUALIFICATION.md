# Capability and release qualification

This edition reflects the staging source audited on 8 October 2026. Its base revision was `d8b8b93345675798f88e80fbca48b4a2a7d3ed8d`, with additional local changes present. It is not a sealed release artifact.

A source feature, green CI, a built wheel, a healthy container, and an accepted user journey are separate facts. Record evidence for the exact artifact and configuration being promoted.

## Present capability boundaries

| Area | Current implementation | Qualification boundary |
|---|---|---|
| Knowledge | Typed rooted records and explicit relations | Validate scope, traversal, cascade, and restore on the selected adapter |
| Collaboration | Workspace/resource roles, direct grants, exclusions, shares | Include revocation, public-read exceptions, and cross-workspace denial |
| Modeling | Authoring YAML v3, runtime schema v2, drafts and migrations | Test existing-record impact, interrupted migration, and failed-item retry |
| Apps | Packages, lifecycle, active definitions, queries, operations, extensions | Test actual dependencies, trust policy, pause, upgrade, and uninstall |
| Experience | Views, App Home, dashboards, files, conversation | Browser readback must agree with authorized query/record results |
| Resident | Default Pydantic AI binding; jvagent compatibility | Qualify actual providers, BYOK modes, approvals, and tool outcomes |
| Native durable chat | Implemented rollout paths; off by default | Process interruption, event replay, cancellation, multi-client isolation |
| Durable work | Leases, fencing, effect IDs, outbox and approvals | Production PostgreSQL contracts and external-effect reconciliation |
| Work mandates | Reviewed contract and guarded foundations | Public admission/execution remains incomplete and non-runnable |
| Retrieval | Scoped text/index paths and backend-dependent semantics | Do not infer universal vector search from pgvector installation |
| Connectors | Native and MCP integration mechanisms | Qualify each actual service and credential flow |
| Attachment screening | Configurable adapter | Default noop is not malware screening |
| Usage | Physical observations and source-aware reconciliation | Estimates/unavailable cost do not establish definitive billing |

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
