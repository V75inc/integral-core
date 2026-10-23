# Capability and release qualification

This guide describes how to qualify a Core change; it is not a live release-status page. Use the candidate-specific release record for actual results; earlier checks do not qualify today's source or artifact.

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
| Retrieval | Real encoder/store readiness, source coverage, current authorization, passage accuracy, budgets and end-to-end latency; qualify each adapter separately |
| Connectors | Each actual service, credential flow, revocation and failure behavior |
| Attachment screening | Configured adapter behavior; a no-op is not malware screening |
| Usage | Physical observations and source-aware reconciliation; estimates or unavailable cost do not establish definitive billing |

## Source gates

Run `make verify` for the broad local gate and `make verify-ci` for CI-faithful smoke testing. The latter reproduces only the smoke lane; PR CI also runs Core-only, extension-contract, PostgreSQL, frontend and artifact checks. `make verify-pr` reproduces the backend test lanes and PostgreSQL lane. CI uses TESTING, no developer `.env`, and xdist for smoke. Run `make verify-core-only` to establish the Core/App boundary.

Run relevant PostgreSQL contracts with a disposable database and temporary test encryption keys. Preserve existing development databases. A green test against an in-memory adapter does not establish transaction/CAS behavior on PostgreSQL.

## Retrieval qualification

Use an isolated database and labelled synthetic corpus. Run the
[real encoder/pgvector and transaction commands](../backend/semantic-indexing.md#qualification)
in addition to ordinary source gates; the default suite can skip those tests.
Verify canonical write → durable intent → encoding → stored passages → authorized
HTTP retrieval → cited source readback. Include long-content tails, edits,
deletion, moves, exclusions, direct Entry grants, revocation and foreign-workspace
denials. Exercise failed delivery, lease recovery, stale approvals and index
configuration rollback. Querying must remain usable during model/store outages.

Measure paraphrase and exact-identifier evidence against labelled source IDs,
including missing-information questions. Record warm/cold conditions, scope,
corpus size, context budget, repetitions and p50/p95 HTTP latency. Compare actual
provider input/output tokens and complete chat time for identical fresh-chat
tasks; embedding-token passage counts are not total provider usage. A small wiki
or 1000-passage SQL probe does not establish production-scale recall or latency.
Keep synthetic corpora and machine-readable measurements with the candidate’s CI
or review artifacts. Do not add dated screenshots and raw run logs to the reader guides.

## Artifact gates

Build a clean wheel, inspect its contents, install it into a clean environment, initialize a blank distribution, and run its packaged UI. Check pinned dependency resolution and explicit index provenance. Package publication requires its own release authorization and readback.

## User-flow gates

Test authentication, workspace switching, record create/read/update, typed relation/member/file presentation, comments, sharing and revocation, views, App Home, dashboards, resident answers, staged application, and result readback. Include a second principal and a second workspace.

For enabled durable paths, interrupt a worker before and after effect boundaries. Verify lease loss, event replay, current-grant rechecks, uncertain-effect reconciliation, and final product transcripts. Confirm that canceling in the UI reaches the backend rather than merely hiding a stream.

## Promotion record

Record the exact source revision, wheel digest, lockfile, database adapter, non-secret settings, provider routes, commands/results, browser journeys, remaining defects and recovery method. CI, local checks, publication and deployment are separate gates.

The current [release qualification](RELEASE_QUALIFICATION.md) records the candidate under review. [Hardening follow-up](../product/HARDENING_FOLLOW_UP.md) preserves outstanding acceptance work. Retired dated reports remain recoverable in Git history. Retained candidate evidence is linked from its topic guide; it does not qualify a different source or deployment.
