# WP-07 live resident app journey

**Status:** live browser evidence for one disposable local workspace. This is
not full WP-07 completion evidence.

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

## Result

| Assertion | Result |
| --- | --- |
| Design stays a proposal until a single explicit build confirmation | Pass |
| Build produces an independently navigable App and six expected Tracks | Pass |
| Materialized data is visible through ordinary App and Track surfaces | Pass |
| Exact fields and seeded row render in browser | Pass |
| Direct record update survives deep-link reload | Pass |

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

## Remaining WP-07 proof

- Cross-user collaboration, conflict, permission revocation, reconnect, and
  deep-link checks against persisted data.
- Approval decision and changed-record receipt linkage through reload and
  failure/retry paths.
- Browser assertions for dates, grouping, dashboards, and views from the
  shared effective definition.
