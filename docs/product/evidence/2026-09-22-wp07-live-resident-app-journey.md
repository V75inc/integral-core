# WP-07 live resident app journey

**Status:** live browser evidence for one disposable local workspace. Combined
with deterministic conflict, retry, and reconnect evidence, this journey is
part of [the WP-07 closure record](2026-09-22-wp07-closure.md).

**Candidate context:** `codex/schema-revision-binding`, local application,
2026-09-22.

## Journey exercised

1. Started from a recorded Car Rental Management design in the resident chat.
2. Requested a build. The resident rendered the proposal and asked for one
   explicit build confirmation.
3. Confirmed the proposed design once. The resident issued the batch and later
   produced its receipt-backed completion message.
4. Opened the ordinary Apps surface. A fresh read initially returned `0 apps`
   while materialization was still in progress; the next fresh read returned
   one **Car Rental Management** app.
5. Opened that App and confirmed six persisted tracks after reload:
   Cars, Registrations, Renters, Rentals, Service Records, and Document
   Renewals. Each showed one seeded entry.
6. Opened Cars and verified its materialized fields and row: Make, Model, Year,
   VIN, License Plate, Status, Current Mileage, and Notes.
7. Opened the Sample Car, changed its operational `Status` from `Available` to
   `Rented`, received the **Entry updated** receipt, then reloaded the deep
   link. The detail panel continued to show `Status: Rented`.
8. Created a separate disposable local user and added that user as an App
   commenter through the ordinary Collaborators panel.
9. Confirmed that the collaborator starts in their own personal workspace,
   where the Administrator App is not implicitly mixed into the current Apps
   list. Opening the shared App link switched scope to **Administrator**;
   the normal Apps list then showed **Car Rental Management**.
10. Opened the same Cars deep link as the collaborator. The Sample Car and its
    persisted `Status: Rented` rendered after the scope switch. Commenter
    controls were present, while entry edit, new-entry, and track-management
    controls were absent.
11. Reloaded that shared deep link and selected **Cars - Kanban by Status**.
    The grouped view showed `Rented: 1` with Sample Car, and empty Available
    and In Service columns.
12. Removed the disposable collaborator and retried the same deep link. The
    resource did not load, proving the direct grant was revoked. The browser
    initially rendered a generic failure label; the detail page now reads the
    canonical API `message` field for this path, with a focused regression
    test for the `Access denied` envelope.
13. Opened Service Records and verified its seeded `Service Date` and `Next
    Service Due` both render as September 22, 2026. Its Calendar view placed
    Sample Service Record on September 22 and exposed accessible date filters
    and month/week/day controls.
14. Confirmed the generated App had no dashboard because the approved design
    did not request one. Its empty dashboard surface clearly offered manual,
    template, and agent-suggestion creation paths. Created a manual **Fleet
    Overview** dashboard, received the `Dashboard created` receipt, and
    reloaded the App with `Dashboards 1` and Fleet Overview still selected.

## Result

| Assertion | Result |
| --- | --- |
| Design stays a proposal until a single explicit build confirmation | Pass |
| Build produces an independently navigable App and six expected Tracks | Pass |
| Materialized data is visible through ordinary App and Track surfaces | Pass |
| Exact fields and seeded row render in browser | Pass |
| Direct record update survives deep-link reload | Pass |
| App-level collaboration is usable from a separate principal | Pass |
| Shared App and record deep links select the accessible workspace | Pass |
| Commenter surface withholds edit and creation controls | Pass |
| Shared grouped view reflects the persisted operational status | Pass |
| Revoked collaborator can no longer load the shared record | Pass |
| Date field materializes into the intended Calendar day | Pass |
| Dashboard creation from the empty state survives App reload | Pass |

## Observation requiring continued work

The first assistant response after authorization said the app was “being
built” and included the phrase “once the build is complete and approved,” even
though the user had already approved it. The eventual terminal message was
correct and the records were persisted. The intermediate phrase is imprecise
status language and should be tightened when the resident’s non-terminal
progress rendering is next revised.

The initially observed `0 apps` was also a read before asynchronous
materialization finished; the next fresh read and reload both proved the
result. The surface should make that transition explicit enough that a user is
not left to infer whether their authorized build succeeded.

## Completion note

The final browser session reconfirmed the persisted Service Records row and
date fields. The closure record joins this live evidence to deterministic
conflict, failed-execution, retry-without-duplication, and reconnect tests.
