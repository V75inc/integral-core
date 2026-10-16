# Existing chat-attachment filing recovery — candidate

## Scope and reproduced failure

Baseline: `7e16cda1`, isolated `codex/attachment-filing-recovery` worktree.
The final candidate also includes the pending design/tag increment for combined
qualification; the component results below preceded that source combination.
No deployed source, stored runtime data, dependency or attachment bytes changed.

Two native PostgreSQL regressions failed on the original service: a successful
filing followed by failed approval completion had no exact applied receipt; an
injected receipt-storage failure still left filing consumed/applied. Those tests
exercised the actual staged dispatcher and real graph records. See
`/tmp/core-attachment-filing-red.xml` and `.log`.

## Implementation

The existing `attach_uploaded_file_to_entry` facade retains its read-only
preflight/error response contract and delegates mutations to a generic service.
The service repeats admission at the effect boundary. Entry revision, file
ownership flag, Entry attachment list, attachment relationship, lifecycle event
intent and exact `attach_uploaded_file` receipt join one PostgreSQL transaction.
It reuses typed Entry update validation without triggering custom-field hooks.

The original chat relationship remains, and no byte storage/accounting operation
runs. The attachment's primary owner/scan facts and the source thread are read
under their row locks. A single source thread must belong to the acting user and
the target's current workspace. Its one CATALOGS edge must reach that workspace's
ChatThreads branch, which must have its one CONTAINS edge from that workspace.
This follows real `create_thread` behavior. The first candidate's invented user
OWNS requirement was removed after existing creation-flow tests exposed it;
normal chat creation supplies the workspace catalogue, not that edge.

The Entry's attachment set is computed under its row lock and written through
its revision fence. Event delivery is offered after an owned transaction commits;
an outer transaction leaves delivery to the existing outbox recovery mechanism.
A lost completion reads the applied receipt and current authorized Entry state,
without filing again after `owner_kind` became `entry`.

## Component qualification

Expanded native component/result-access selection: **40 passed** (11 filing
cases and 29 existing result-access cases), zero skipped/failures/errors,
`/tmp/core-attachment-filing-recovery-final.xml`. It covers lost completion and
receipt recovery, receipt-fault rollback of both nodes/edges/event intent, primary
already-filed and blocked source facts, detached catalogue, foreign source
workspace and ambiguous source parents. Two independent connection pools also
race different approved Entry targets for one source attachment: one approval
succeeds, exactly one Entry revision advances, and only one Entry home exists.
Cancellation after graph commit recovers without refiling, and revoked
workspace authority refuses execution or withholds current Entry/file readback.
Fixtures use real chat creation. A supplementary cancellation-before-commit
probe passed **1/1**, rolling back both nodes, edges and event intent before
one clean retry (`/tmp/core-attachment-cancel-native.xml`).

Existing filing tests: **9 passed**, zero skipped/failures/errors,
`/tmp/core-attachment-filing-default-backend-cwd.xml`. Their earlier unrooted fixture
was updated to supply real authentication/profile, workspace membership,
App/Track containment and Entry type. The existing rollback and retained-chat
readback contracts remain asserted. The filing event now includes the canonical
Entry record revision in addition to its existing attachment list.

## Aggregate qualification and parent alignment

The final combined source passed full backend: **6,593 passed / 878 skipped**,
zero failures/errors (7,471 collected, 4,193.580 seconds;
`/tmp/core-design-tags-filing-full.xml`). CI-faithful passed **1,797 / 22 skipped**
(`/tmp/core-design-tags-filing-ci.xml`). Combined native passed **75/75**
(`/tmp/core-design-tags-filing-native.xml`): 16 tag, 15 design, three release,
11 filing, one cancellation and 29 existing operation-result cases. Overlapping
component counts must not be added to these aggregate counts.

All four independent artifact gates passed on the frozen source snapshot;
reproducible Core wheel SHA256:
`14433df22fe37150e88d4eb0b614eea2e5453c423c41988b9bf951f2a1377023`.
Logs: `/tmp/core-design-tags-filing-artifacts.log`. The sealed source manifest
`/tmp/core-design-tags-filing-source.sha256` matched before parent alignment;
changed runtime/test files remain byte-identical afterward. The filing-only
candidate is aligned onto `d1212962`, preserving the already-qualified design
and tag changes and their newer documentation rather than reintroducing old
pending-status claims. Pinned hooks and staged substrate guards passed and are
rerun before the final commit.

The nine-case default filing selection also passed from the canonical backend
working directory. Legacy chat sources without canonical catalogue containment
require reviewed repair; no authority edge is invented. Local JSON mode does
not gain a cross-process transaction/rollback guarantee. Arbitrary uploads,
external bytes, mixed batches and the rest of the hardening goal remain open.
No deployment or runtime-data change was performed.
