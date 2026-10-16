# Local Business hardening deployment and smoke — 2026-10-13

## Deployed identity

- Business feature source: `d9b18c113380815f9ea4fb4d398be7e62d125d0f` (commercial-access branch, unchanged).
- Core: `0.1.1rc18`, base `8af5d9ca1605154435966ac0e6491ff3330cdf50` plus the uncommitted disclosure and page-context amendments in `/private/tmp/integral-core-hardening`.
- Core wheel SHA256: `bd1fcb713855b4f04fdb284a6b21acf942de56cb862af4aace02966a1baa9935`.
- API image: `integral-business-route-context-api:qualified-local`, `sha256:cfaf202c77a78d9af561331339f961c55c70c7d1dbb33865e0d2c21b1c0fb037`.
- API extends the existing qualified local Business image, installs the reviewed wheel without changing dependencies, and passes `pip check`. Verified jvspatial 0.2.2, pydantic-ai-harness 0.36.0 and pydantic-ai-slim 2.54.0. Changed installed Python module hashes match source and wheel.
- Wheel includes the portable resident skills and built neutral Core UI. Separate web image includes the Business host overlay and Recruitment public assets. Browser reload confirmed `/assets/index-DqtELZJ3.js`.
- Runtime remains `integral-business-rc17` to preserve volumes: web 9114, API 4414, existing PostgreSQL 5438. Commercial access mode remains `full`.
- Fresh 28 MB PostgreSQL backup: primary Business `.integral/local-deployment/route-context-pre-upgrade-20261013.dump`. Backup created successfully; restoration was not rehearsed in this task.
- Restart helper: primary Business `.integral/local-deployment/route-context-up.sh`. This starts API/web from the reviewed images with existing configuration, without rebuilding or replacing the database.

## Browser and live provider validation

| Probe | Observed result |
|---|---|
| Current CRM App / section | `integral_get_page_context`; correctly identifies CRM, Tracks section and route |
| Navigate CRM to Marketing in the same chat | Fresh context identifies Marketing; does not reuse CRM identity |
| Campaigns saved view | Correct Track/App, saved view Campaigns, type table, three canonical visible entries |
| Rentals saved view | Correct owning App Fleet & Rental Manager, Track Rentals, view Rental Board, type kanban and grouping |
| Explicit CRM target while viewing Fleet | `integral_list_tracks` called with CRM App ID; returns Communications, Opportunities and Contacts, total 3 |
| System-prompt story extraction | Fixed public refusal; no private instruction reconstruction |
| Existing Campaign entry dialog | Opens and closes normally; no data change |
| Rental Board to Calendar and back | View changes and board remounts with its four cards |
| Kanban to Approvals to Home | Route and rendered content both change; no navigation freeze |

Live provider display: `ollama_chat/deepseek-v4.1-flash`. Page-context responses explicitly distinguish send-time client snapshots from live screen/current query facts. Campaigns read receipt: run `27f33912-b9d1-42e4-ab84-7a80cde91e69`, `resource_verification=current_acl`, `freshness=current_turn`. Explicit-target read run `497697cc-d15e-4129-8b2b-605c97d7d911`; succeeded, not replayed.

Both inspected tabs returned zero console errors/warnings. The API is healthy and startup completed without the missing-skills error after packaging repair. The initial incomplete-wheel attempt failed a chat and was corrected before qualification; its failed message remains in the dedicated smoke chat as evidence.

All 28 entries, 11 Tracks and 3 Apps preserved. Combined record count/digest before and after final smoke: `42|c6436739909d40621baa8d7c8937b124`. This is a read-only smoke: chat messages and execution receipts were created, but business records were not mutated. Approvals shows zero pending items.

## Source qualification and limits

The previously completed source qualification remains 860 PostgreSQL-backed backend tests and 1,739 frontend tests passed; see the page-context report. These counts were not rerun for packaging-only deployment work.

This is local validation, not a release or exhaustive security/provider evaluation. Entry-dialog awareness is covered by source projection tests, but no live chat was submitted while the modal blocked the composer. No approved seeding/mutation, drag-to-move or recovery journey was exercised. Calendar rendered an empty state; rental-date placement was not qualified. Provider costs were unavailable in the UI; accounting accuracy is not established here.

No new commit, push, PR edit, merge or external deployment. Primary Business remains clean main; existing draft PR 18 and Core PR 136 remain unchanged.

Evidence: `tasks/route-context/evidence/local-deployment.json`, `live-chat-smoke.txt`, `navigation-smoke.txt`, `kanban-context-smoke.png`.
