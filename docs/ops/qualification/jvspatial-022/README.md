# JV Spatial 0.2.2 local Core qualification — 2026-10-13

Local deployment: http://127.0.0.1:9016 (API 4016), PostgreSQL database
`integral_jvspatial022` on the existing local review PostgreSQL service. The
previous review database was preserved. Normal authentication and workspace
permissions were used; no test-auth bypass was enabled.

## Published release follow-up

JV Spatial PR #59 was merged and version 0.2.2 published on 13 October.
Core 0.1.1rc18 now pins the public package in its metadata, lockfile, and Docker
build assertion. The record below describes the earlier local candidate; see
[current qualification guidance](../../QUALIFICATION.md) for the final combined
source and published dependency checks.

## Original artifact and source scope

The running environment contains the built JV Spatial **0.2.2 wheel from PR
#59, commit 07c1099**. At qualification time that PR was open and the 0.2.2 PyPI
endpoint returned 404. This is local artifact qualification, not proof of an
installed published release. The normal repository dependency pin and lock remain
0.2.1 until the public package is available. The locally built Core candidate
`0.1.1rc17+qa.jvspatial022` explicitly pins JV Spatial 0.2.2; its wheel and
`uv pip check` pass. Both running services load installed wheels, and
`integral web` serves the current compiled frontend assets. Wheel SHA-256 is recorded in
[runtime-and-fixtures.json](runtime-and-fixtures.json).

Build inputs: primary Core backend working checkout, including the native
transaction scope with its compatibility adapter removed; frontend from the
existing `codex/home-layout` working checkout, including the approved dashboard
and chat work. The final deployment is a built local artifact from these
uncommitted development sources, not a merged release or Docker image. All
Admin views were checked again against the installed wheel.

The runtime turn token budget is 1,200,000. Storage encryption uses the existing
local installation key. Credentials and keys are kept outside these artifacts, in the private local
deployment directory under `~/.codex/local-deployments/`.

## Data and independent expected answers

The fixture has 23 accounts, 23 personal workspaces, 22 apps, and 24 tracks.
One ordinary user is an invited editor of only **Personal Life Manager** in the
administrator's workspace. That user can read its three tracks, not the other
21 tracks. An unshared track contains a `PRIVATE-CANARY-022` record.

| Track | Data |
| --- | --- |
| Businesses | Five named businesses plus 18 QA distractors, 23 records total |
| Expenses | Walmart 120; McDonald's 35; Amazon 85; Shell 60; Berghotel Grosse Scheidegg 200; coffee 8 |
| Contacts | John Smith, Sarah Johnson, ABC Construction, XYZ Supplies |

Five expenses carry stored business `REFERENCES` edges. Coffee has no business
relation. Four businesses carry contact `REFERENCES` edges. The largest expense
links to Berghotel Grosse Scheidegg, then to XYZ Supplies. Independent expense
sum: **508**, six records, five distinct referenced businesses.

## Browser and API results

- Normal administrator and ordinary-user sign-in/sign-out succeeded.
- All five Admin views loaded. User and workspace pagers returned 20 + 3 rows;
  app pager 20 + 2; track pager 20 + 4. No overlap on the checked user,
  workspace, and track pages. User search returned one matching record.
- Browser testing exposed and repaired two Core metadata defects: workspace
  counts followed the obsolete direct containment layout, and app owners stored
  as auth principal IDs did not resolve to graph profiles. Counts now follow the
  canonical workspace branch registries through a Walker; owner summaries use
  the existing principal-to-profile batch resolver.
- Global Tracks loaded for the administrator (24) and invited non-admin (3).
  All five Admin APIs and the unshared canary record returned 403 to the member.
- The invited editor saved a contact-field change and restored the original value.
- Agent citation navigation opened the contact record and its incoming business
  reference. Apps navigation worked after closing the record modal.
- Home submission started a turn without a second submission. Sidebar suggestion
  menu opening did not move the input. Dashboard top accents share the primary
  theme color; title/logo are neutral in both light and dark modes.
- Profile order is avatar, display name, email, password, timezone. Device
  timezone resolved to America/Guyana; `/profile` resolves to Settings Profile.
- An entry-detail duplicate React key warning was repaired by assigning separate
  keys to primary, editing, and secondary related-view sections. The repeated
  browser open/edit/cancel flow produced no new console errors after the fix.
- All Admin directories were reread with observability explicitly enabled and
  DEBUG active. No routine successful database observation logs, missing
  PostgreSQL-method exceptions, or stack traces occurred in that final readback.

Detailed checks: [browser-checks.json](browser-checks.json),
[non-admin-boundaries.json](non-admin-boundaries.json),
[logging-readback.json](logging-readback.json).

## Live agent results

Ollama Cloud `deepseek-v4.1-flash:cloud` used the actual stored graph. Tool and
terminal receipts were retained in an operator-local log outside the repository.
The checked graph fixtures and runtime identity are recorded in the JSON artifacts above.

The first administrator turn correctly returned 508, the unlinked coffee
purchase, and the hotel → business → XYZ Supplies relation chain with source
links. It called `integral_get_graph_context`; all seven model calls responded.
The browser usage receipt showed 127,146 input + 3,345 output = **130,491 tokens**,
10 tool steps, and approximately 27 seconds. Provider cost was unavailable.
A follow-up correctly enumerated five distinct linked businesses.

The ordinary-user agent listed exactly the three accessible tracks, returned
508 using the expense aggregate, and refused the unshared QA app. Its answer did
not expose the canary. Sharing the owner's model key did not widen graph access. A final graph-context
turn on the installed wheel again returned the hotel → business → XYZ Supplies
chain with three valid source links.

**Model-grounding limitation:** both administrator answers added an incorrect
incidental description of the distractors (19 rather than 18, and a fabricated
QA title range). Requested totals, links, and distinct-business count were
correct. This is a remaining answer-quality finding, not a missing database API
or a fully clean model evaluation.

## Automated checks and limits

- 35 Core PostgreSQL tests passed: Admin, new inventory regression, transaction
  scope, native transaction API, access directory, and database logging.
- 21 frontend tests passed for Home, composer handoff, and layout navigation.
- 41 entry detail and related-view tests passed after the React key repair.
- TypeScript checking and production frontend builds passed (existing chunk-size
  advisories remain). Backend changed-file formatting and configured lint passed.

This run did not execute the entire Core `make verify` gate, publish 0.2.2,
commit/push Core changes, qualify a production deployment, or inject interrupted
provider/worker failures. Local timings are not production performance guarantees.

Screenshots: [Admin overview](admin-overview.png), [Home light](home-light.png),
[Home dark](home-dark.png), [invited tracks](invited-user-tracks.png),
[ordinary-user agent](non-admin-agent.png),
[packaged Home graph answer](packaged-home-graph.png).
