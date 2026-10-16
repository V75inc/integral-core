# Connector operability and connected-row UI — 2026-10-16

## Scope

Generic Integral Core connector identity, MCP invocation boundaries, error reporting, response timeouts, and connected-row layout. Based on PR #136 at `fe322849badfdeac7bbb8fbb90f2efd274952fc9`. This change set extends the existing PR #136 branch; no merge or release is implied. Business source is unchanged. The wider hardening goal remains paused.

## Confirmed causes

1. A real installer saves `Connector.owner` as its login-account ID, while the delegation ceiling re-evaluates its issuer by graph-profile ID. The human connector policy compared the raw stored owner to only the caller's profile ID. Valid current ownership therefore returned `delegator_ceiling_denied` before any remote invocation.
2. Personal connector resolution, list traversal, row management, OAuth completion, and UI ownership affordances also contained raw-ID comparisons. Existing connector-read routes could enter a generic user-scope read branch before the connector-specific authorization check.
3. `McpProxyError` was not normalized by the common tool dispatcher; meaningful connector refusals became `internal_error` and the generic dispatch sentence.
4. Stdio MCP sessions did not configure the SDK response timeout. The HTTP transport timeout alone did not bound MCP protocol responses on both transports.
5. Once the authorization defect was repaired, the actual saved Serper key returned HTTP 403 with `Unauthorized.` The already configured service-level key returned HTTP 200 for the same public search. This is a separate credential issue, not an empty search or a reason to bypass authorization. No key values are included in evidence or this report. The final browser run reported `remote_authentication_failed` / HTTP 403, with an explicitly failed, non-replayed receipt. The user subsequently authorized using the existing server key. Only this local connector's saved key was replaced through the encrypted connector-update service; owner, workspace, scope and permissions were preserved.

## Implementation

- Resolve current owner/caller identity through the canonical resolver; retain current policy, membership, owner lifecycle, and connector-subject delegation checks.
- Apply connector-specific authorization before generic scope-read fallbacks. Normalize API and UI ownership aliases without migrating stored identities or creating new grants.
- Preserve typed connector error codes at common dispatch.
- Accept routing metadata only from trusted registered specifications/keywords. Refuse input-supplied MCP routing selectors and cross-workspace invocation before remote calls.
- Set the SDK response timeout for both stdio and HTTP sessions, including initialization.
- Evaluate protocol `isError` before payload projection, after closing the session normally. Classify recognized HTTP 401/403 and 429 tool failures into fixed actionable messages, never echoing arbitrary remote error text or credential-bearing URLs. Unknown failures remain failures. No automatic replay of external effects was added.
- Replace the connected row's vertical right-hand stack with a full-width identity header and compact footer. Tools and connection checking remain visible; refresh, reconnect, edit, and delete use the shared overflow menu. Keep delete confirmation and server authorization.

## Qualification

- Pre-push full default substrate backend gate: **6,630 passed, 933 skipped, zero failures/errors** (7,563 outcomes; normal default `not domain_app and not slow` selection). `make verify-ci` also passed with `TESTING=1`, `DEBUG=false`, and xdist.
- Staged pre-commit framework passed formatting, configured runtime lint, drift checks and mypy. Full staged substrate guards and TypeScript check passed.

- Initial regressions reproduced the mixed-identity denial and generic error conversion.
- Broad connector/policy regression: **513 passed, 2 optional skipped, 3 legacy domain-fixture cases explicitly deselected**. The three excluded cases also fail on the unchanged PR head because the old CRM/GitHub App fixture packages are absent. No domain packages were introduced into Core to satisfy those tests.
- Final MCP/delegation regression after actionable error classification: **150 passed**.
- Final PostgreSQL connector/delegation/client regression: **69 passed, zero failures/errors/skips**.
- Frontend: **1,829 passed across 321 files**; connector panel **14 passed**; TypeScript check and commercial-host production build passed.
- Pinned Python formatting/lint and diff whitespace checks passed for touched runtime files.
- Local API wheel files were compared with the built wheel (815 packaged files). Database container and persistent volume were retained; a fresh restricted backup was saved before restart.
- Browser after reload: connector discovery, connection check, two-tool inspector, overflow menu, and page-fetch invocation passed. Actual Serper search reached the provider and exposed the rejected saved key. Service-key authentication was separately verified directly against the same provider endpoint, then search and fetch both succeeded in-browser after the authorized saved-key update.

## Evidence

Local logs and XML are under `/private/tmp/core-connector-*`. Deployment and source identity are recorded in `/private/tmp/core-connector-fixed-deployment.json` and `/private/tmp/core-connector-operability-preview/source-identity.json`. The source snapshot, local diff, wheel, and Dockerfiles are retained in that preview directory. No credentials should be copied into PR messages or shared logs.

This is connector-focused qualification, not a new full-Core release or completion of the paused hardening backlog. The authorized server-key switch is now verified through the saved connector in the browser: search returned five results for Eldon Marks, then fetch retrieved https://eldonmarks.com/. Persisted receipts `o.RunStep.13d80ce5939442c28144b28e` (search) and `o.RunStep.542bc34032624a489febeed5` (fetch) both show `succeeded`, policy `allow`, and no error code. Persisted connector health is `ok` and `last_error` is null. Evidence: `server-key-smoke.json` in the artifact directory. No workspace records were modified by the smoke test.
