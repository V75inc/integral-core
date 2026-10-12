# Account time zones

Settings → Profile → Time zone stores an IANA identifier on `User.timezone`.
Every authenticated user can manage their own zone. An unset account uses UTC
until an authenticated browser detects and saves its device zone. A conditional
write (`only_if_unset`) protects saved choices across devices; traveling does
not silently change the account. Use the device button and Save to change it.

`GET /api/users/me/timezone` returns `{timezone, configured}`. `PATCH` accepts
`{timezone, only_if_unset?: boolean}` and rejects unknown fields and invalid
IANA identifiers. Identity comes exclusively from authentication. Profile
exports also include the stored `timezone` (null before detection). The setting
is separate from the general preferences dictionary so editing a bio or other
preferences cannot clear it. No graph migration is required for old profiles.

Backend consumers call `app.services.user_timezone.get_user_timezone(principal_id)`.
The resident harness receives the account zone and current local date/time from
Core. `integral_plan_query` resolves relative dates in the account zone when no
explicit zone is supplied. Frontend consumers can read `useAuth().user.timezone`
and use `formatUserDateTime` for account-local displays. Audit timestamps,
leases, persistence and due-time comparisons continue to use UTC.

New routines inherit the account zone unless given an explicit override. Staging
resolves and displays that zone before approval and saves the resolved value in
the proposal. Applying it after a preference change retains the approved zone.
Existing routines keep their own zone; the account setting is not a bulk schedule
edit. Routine edits likewise retain their saved zone unless explicitly changed.
Cron evaluation uses IANA daylight-saving rules. One-shot `run_at` timestamps
with an offset are absolute; timestamps without an offset use the chosen zone.
Ambiguous or nonexistent local times require an explicit offset. Routine service
callers without a configured account retain the UTC fallback.

App-bundled schedules with explicit zones retain their declared semantics.
Date fields alone do not schedule a reminder; the routine surface remains the
mechanism that creates and dispatches reminders.

Preserved invariants: I-GRAPH-01/02 (reuse the rooted User and existing routine
edges), I-SUBSTRATE-01 and I-EXT-01 (generic account timing, no App coupling),
and existing principal/workspace authority boundaries (self-only setting, no
new tool or scope grants). Native database conditional updates protect automatic
inference against concurrent manual writes. JSON/memory development fallbacks
use a process-local lock and do not claim multi-process safety.

Validation: `test_user_timezone.py`, `test_routine_tasks.py`,
`test_query_plan.py`, `TimezoneSetting.test.tsx`, and
`AuthContext.session.test.tsx` cover persistence, invalid inputs, own-account
access, inference/manual races, local one-shots and DST rejection, inherited and
explicit zones, staging snapshots, relative query windows, UI retry and offline
detection. The inference race also passed on isolated PostgreSQL. These checks
do not establish deployment or live-provider reminder delivery.
