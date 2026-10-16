# Six-approval App update qualification — 13 October 2026

## Findings and changes

Untemplated Tracks previously used their mutable display title as the relation
Track type. Renaming a Track therefore rejected an unchanged reference when an
existing Entry was updated. New ad-hoc Tracks now persist `relation_type_key`;
formal `template_id` takes precedence. Track display updates preserve this key,
and relation validation, lookup and UI selectors use it before the display title.
It is server-managed metadata, not an accepted client update field.

Legacy Tracks can recover the initial identity from exactly one retained Core
`track.create` snapshot matching the Track ID, workspace and creation timestamp.
This bounded lookup never guesses from the requested relation or model history,
never grants read authority and never rewrites the target on a relation read.
An authorized Track metadata update persists the recovered key. Without matching
retained evidence, the legacy title fallback remains; an already-renamed Track
whose creation evidence is missing needs a reviewed identity migration. This is
not a blanket migration of historical schemas authored after earlier renames.

The agent rate limiter pooled polling, token preflight and mutation requests in
one per-IP `/agentive` bucket. Passive GET/HEAD requests now have their own budget;
token preflight and writes share the action budget. Each budget retains the
configured `RATE_LIMIT_AGENTIVE` count/window (default 120/60). Neither is unlimited.
Token rotation cannot create a new budget. Auth/MCP/chat limits, proxy validation
and the existing process-local deployment limitations are unchanged. Poll
starvation was reproduced with the limiter test; the original reported 429 has
no captured historical response trace in this local stack.

Failed, explicitly unsuccessful or skipped executions are not consumed. Their
approval remains recorded, execution claims are released, and the failure is
persisted. The review offers **Retry application** for an approved proposal.
If execution was confirmed consumed but the later review update fails, the UI
reports that the change was applied and asks for status refresh, not reapproval.
No writes are automatically retried on a network/rate-limit error.

## Qualification

The supplied Personal Life Manager scenario runs through actual scoped staging,
executors, permission checks and record readback in disposable test databases:

1. Create one App, Businesses/Expenses/Contacts, and the 15 specified entries.
2. Stage all six update proposals before applying any of them.
3. Apply the App rename, two Track renames and three Entry updates.
4. Verify the same App/Track/Entry IDs and counts, unchanged Expenses Track title,
   expense amount 150 and its original Business reference, and both Entry renames.
5. Verify all successful tokens consumed and prior failure state cleared.

Three variants pass on JSON and PostgreSQL: new Tracks, legacy Tracks before
rename, and a legacy Track already renamed before the patch. In the latter case,
the Expense is retried before a Track metadata update persists the recovered key.
An incompatible formal template still produces the reported Track-type error;
fields remain unchanged, the proposal remains approved/unapplied, and explicitly
retrying the same token after restoring compatibility succeeds without duplicates.

Limiter coverage exhausts passive reads, then admits six complete token-preflight,
approve and review-update triplets. Further actions are rate-limited, including
reads of a different token. Source relation/admission and staging regressions pass;
UI tests cover failure/partial progress, application retry labels and a 429 after
a confirmed application. Formatting, lint, configured types and substrate guards
are qualified separately from release CI. These are deterministic tool-path and
state tests, not provider-generated prompt or production-capacity acceptance.

## Local rollout evidence

Evidence and logs are retained under `/private/tmp/integral-browser-latency`:
`approval-final-tests.log`, `approval-state-final-tests.log`,
`approval-postgres-tests.log`, `approval-ui-final.log`, `approval-hooks.log` and
build/deployment logs. Test data is isolated from the user stack. Local images and
source/wheel checksums are recorded in the ignored Business deployment artifacts.
The existing local database/volumes are preserved; no live App copies, approvals
or business records are created by this qualification. Existing failed approvals
must be reconciled against their actual token state and current records, rather
than replaying already-consumed changes.

The local rollout uses `integral-business-approval-update-api:local` and
`integral-business-approval-update-web:local`, based on Core `71ec0c55` plus
the uncommitted collaborator/approval fixes. The rc18 wheel SHA256 is
`78cd6c4f3108bb79c4f1dc21170bed79165b231e79b2d0935978367410b63d2b`.
The running API reports Core `0.1.1rc18` and jvspatial `0.2.2`; all nine
changed backend production file hashes match the artifact manifest. Before/after
readback of the 59 existing App/Track/Entry rows has the same content digest.
The browser loaded `/assets/index-CPv4cELK.js`, matching the overlay build,
rendered Background Tasks in the existing session and captured no console errors.
This browser reload does not constitute a live six-approval acceptance run.
