# WP-07 staged-change status evidence

**Status:** focused implementation and deterministic evidence. Superseded for
completion status by [the WP-07 closure record](2026-09-22-wp07-closure.md).

**Candidate context:** `codex/schema-revision-binding`, 2026-09-22.

## Corrected state contract

The Approvals page has two queues: policy approvals and changes staged in a
conversation. A staged change can be authorized before its executor has run.
The page previously described authorization as an immediate application, which
could falsely tell a user that a record was already changed.

The staged queue now distinguishes the two states:

| State | User-facing language | Available decision |
| --- | --- | --- |
| `pending` | `Awaiting authorization` | Authorize or reject |
| `blessed` | `Authorized · waiting for the agent to apply it` | No duplicate authorization |

The page also uses the change's semantic summary rather than foregrounding an
internal operation identifier. The conversation link remains available so the
user can inspect the proposed change and its later receipt.

## Deterministic evidence

| Check | Result |
| --- | --- |
| Authorized state is distinct from a completed write and has no second authorization action | Pass: `StagedChatApprovals.status.test.tsx` |
| Pending state clearly requests authorization | Pass: `StagedChatApprovals.status.test.tsx` |
| Approval-change summary and resource-link regression suite | Pass: focused approval suite |
| Frontend type checking | Pass: `npm run lint:types` |

Focused command executed:

```text
npm run lint:types
npm run test:run -- --run \
  src/components/approvals/__tests__/StagedChatApprovals.status.test.tsx \
  src/components/approvals/__tests__/ApprovalsListBody.presentation.test.tsx \
  src/components/approvals/approvalPresentation.test.ts
```

Result: **3 files, 5 tests passed**.

## Completion note

This document records the initial authorization-language correction. The
closure record adds the persisted failure/retry presentation, receipt,
conflict, reconnect, and live-browser evidence that completed WP-07.
