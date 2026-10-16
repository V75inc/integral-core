# Operation parent-owned reference admission

Date: 2026-10-15. Status: local candidate; system qualification remains open.

## Contract

Operation output already passes a bounded dependency gate under current
identity/workspace admission. The new reusable
`services/parent_owned_resource_scope.py` supplies canonical containment for
comments and filed attachments. Selection is not authorization: callers must
independently check current `entry.read` and the selected request workspace.
No child ID, scalar parent hint, historical receipt or neighboring label grants
access. A denied dependency withholds the whole output.

The service extends `ResourceLocationWalker`; it reads primary node records and
complete bounded parent sets, then follows the existing Entry/Track/App/workspace
root proof. It introduces no endpoint, App-specific behavior, implicit file-byte
capability, or second effect journal.

- A Comment requires exactly one direct `HAS_COMMENT` Entry parent. Threading
  metadata is not an authority parent.
- A filed Attachment requires explicit `owner_kind: entry`, a known persisted
  scan status other than `blocked`, and exactly one Entry home. It may retain
  one original ChatThread edge, as the existing chat-filing service does. That
  edge grants no permission to read or expand the original chat.
- Additional Entry homes, unsupported parent types, missing/dangling parents,
  unfiled chat objects and blocked files withhold disclosure. This does not
  claim support for avatar-only or chat-only attachment output.
- Comment parent reads request at most two edge candidates; filed attachments
  request at most three. The extra candidate detects ambiguity. Fresh canonical
  parent location and current read authority remain independently required.
- Parent-store outages propagate as authorization unavailability, rather than
  being mislabeled as verified denial.

## Reproduced and qualified evidence

The initial four native cases had two expected failures: legitimate readable
comment/file references were withheld by the old unsupported-family behavior.
Private references already remained withheld. Red evidence:
`/tmp/core-operation-parent-red.xml`.

The expanded 21-case native suite passed at
`/tmp/core-operation-parent-expanded-fixed.xml`. It includes readable/private,
orphaned/missing/competing parents, foreign workspace despite actual access
there, filed-chat provenance, blocked/unfiled attachments, outages, and four
independent-connection revocation/reparenting cases. A held participating writer
blocks release; the later reader observes the committed denial.

The first native bound-query probes observed the pool adapter, while release
correctly used a native transaction handle. Their empty observation assertions
failed; this was a test-instrumentation error, not evidence of unbounded SQL.
The corrected probes observe the actual transaction-handle queries and call
through to real SQL. Both bounds and a raw Auth identity output denial passed
at `/tmp/core-operation-parent-query-object-native.xml`.

Initial output and read-cache integration cases retain current parent checks.
Two native command tests also passed at
`/tmp/core-operation-parent-effect-native.xml`: a handler creates a child once,
then later parent-read revocation withholds replay without recreating the child
or changing the successful operation receipt. The final combined native run passed all 48 cases at
`/tmp/core-operation-parent-object-final-native.xml`, including initial-denial
rollback of both child and receipt, dispatcher read-cache integration, native
query limits, Auth-object refusal and the earlier 18 result/function cases.
The final pinned format/lint/type hooks passed at
`/tmp/core-operation-parent-object-final-hooks.log`.

## Object namespace follow-through

Operation dependency inspection now recognizes both `n.*` graph IDs and `o.*`
Core object IDs. Unit probes reproduced the old empty dependency set for an
Auth identity and a credential-object reference. Known object references require
their own supported authority contract; unknown Core object families fail
inspection instead of silently contributing no dependency. This change does not
modify the separately reviewed activity-event snapshot inspector.

All 31 bounded-inspection tests passed at
`/tmp/core-operation-object-reference-green.xml`. The raw Auth object fixture
uses a test-only hash; no live credential was read or emitted. Identity/resource
metadata projection still requires an explicit safe field contract; this is not
permission to return a raw User or AuthUser record.

## Remaining acceptance

- Final combined native and neighboring runs, architecture/format/type checks,
  full source and CI-faithful qualification, and clean public artifact evidence.
- Explicit Entry author/type and supported model/taxonomy metadata projection;
  no blanket exemption for an ID that appears elsewhere in an output.
- Honest opaque applied-outcome recovery when current result data is withheld,
  including preflight revocation. Preserve exact receipt/key/input identity.
- The rest of R1/R2 executor, external outcome, restore and load qualification.

The isolated candidate lives at `/private/tmp/core-operation-parent-projection`,
on top of staged tree `0fd57f931eeaa5a530034d0ffaf53394c2a03e10`. The earlier full
backend run completed on that unchanged baseline in
`/private/tmp/core-hardening-continuation`: 6,414 passed, 637 skipped, no failures
(`/tmp/core-operation-function-full.xml`, 1,224.772 seconds). Its result does not
qualify this newer increment. The increment is being reconciled onto the main
hardening candidate for combined qualification. No commit, push, merge or local
redeployment is claimed here.
