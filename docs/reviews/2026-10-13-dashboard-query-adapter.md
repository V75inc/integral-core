# Core dashboard query adapter

## Contract and authority

Local amendment on `codex/harness-hardening-qualification`, following the CRM dashboard diagnosis. Core remains domain neutral; no Business package or runtime record changes are needed for eligible declarative queries.

An installed App's portable entry query template can supply a dashboard without a separately authored `dashboard` flag when it selects `id`, `track_id` and `title`, supports cursor pagination, has no additional inputs, and has no traversal or initial cursor. An explicit `dashboard: false` remains a refusal. Code/tool handlers still require their explicit dashboard contract.

Core discovers separate per-Track source bindings via `integral_suggest_dashboard`, and derives scalar field types from the visible Track's published model and the query's selected fields. Every page runs the original `invoke_app_query`: workspace, lifecycle, entitlement, policy and resource authority remain in force. Dashboard narrowing cannot broaden the query. Cross-App Tracks are rejected. Hidden Tracks are not offered.

Before aggregates, the adapter completes the query and narrows its rows to the selected Track. It stops at 5,000 scanned rows or 100 pages; duplicates, repeated cursors, malformed pages, revoked authority and incomplete reads produce errors, never partial exact totals. Scope-bound pagination retains the query engine's own cursor/revision semantics. No universal unrestricted record scan was introduced.

## Agent build and validation

- Suggestions expose exact query paths/params/Track identity, published fields and typed output schemas for metric cards, bar/pie/date charts and record summaries.
- Nested business fields now validate and aggregate correctly.
- Dashboard create/update and native staging reject unsupported package scans, mismatched declarations, unknown/non-scalar summary fields, unsupported filter modifiers and nonnumeric numeric aggregates before consent.
- Native dashboard SOP directs the model to discover per-Track sources and copy bindings, rather than invent package queries or use generic scans.
- Existing invalid saved boards are not silently rewritten. Rebuild through normal approval with the newly discovered sources.

## Qualification

82 targeted dashboard, adapter, API, binding, skill and capability-map tests passed (14.19 seconds). Adapter cases cover eligibility, scalar allowlists, pagination, narrowing, scan budgets, repeated rows and late revoked authority. Pinned formatting/import checks, flake8 and diff whitespace checks passed. Configured backend mypy passed on 712 files. An initial ad hoc mypy command used the wrong configuration; the repository's canonical `.ci/mypy_backend.sh` is the reported gate.

Local Core rc18 wheel SHA256: `4e49f31a2c563ebd77b1671380a48d433fa822e3cfc72dc56c4f487aa06a476c`; deployed to the existing Business API image/stack. Resident skill assets bundled. PostgreSQL backup: primary Business `.integral/local-deployment/dashboard-pre-update-20261013.dump`. Pre-update business Entry/Track/App/Dashboard digest: `58|7ddc6f243a3a569b560aa5c6adfa82b0`.

Live qualification is recorded in `tasks/dashboard-query/evidence/`. No commit, push, merge or PR changes.

Live native smoke returned complete previews without errors: Contacts 3, Opportunities 3, Communications 4; the four existing child email-message Tracks each returned 0. These were read-only suggestions, not persisted dashboard edits.

Final live refresh returned nine successful complete widget previews, including Contacts stage groups active/lead/prospect = 1 each and Opportunities lead/negotiation/prospect = 1 each. The misleading zero-total rationale was removed. Business record digest remained `58|7ddc6f243a3a569b560aa5c6adfa82b0`. Saved dashboard remains unchanged and requires a normal approved rebuild. No chart was persisted or rendered as a saved dashboard in this read-only qualification.

Additional unchanged-query and authority regression cohort: 141 tests passed (27.09 seconds), covering query boundaries, QuerySpec and App query policy revisions.
