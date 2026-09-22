# WP-07 approval-review evidence

**Status:** focused implementation and browser evidence. WP-07 remains open.

**Candidate context:** `codex/schema-revision-binding`, 2026-09-22.

## Delivered behaviour

Policy-gated changes are now reviewed as a human-readable change before the
user opens technical data. Each approval row presents:

- a semantic action and affected resource summary;
- the eligible top-level proposed values, excluding internal and nested
  implementation payloads;
- a direct link to the affected App, Track, or Entry; and
- raw payload only through the explicit **Show technical details** control.

This keeps the approval decision attached to the change a person can assess,
while preserving diagnostic details for an operator who needs them.

## Deterministic evidence

| Check | Result |
| --- | --- |
| Semantic action, safe value extraction, and record deep-link construction | Pass: `approvalPresentation.test.ts` |
| Approval row shows proposed fields and resource link before hidden technical payload | Pass: `ApprovalsListBody.presentation.test.tsx` |
| Existing empty, loading, error, and combined approval-queue behaviour | Pass: `ApprovalsListBody.emptyState.test.tsx`, `ApprovalsPage.emptyState.test.tsx` |
| Type and component lint checks | Pass: `npm run lint:types`; focused `eslint` |
| Full frontend test suite | Pass: `npm run test:run` |

Focused command executed:

```text
npm run lint:types
npm run test:run -- --run \
  src/components/approvals/approvalPresentation.test.ts \
  src/components/approvals/__tests__/ApprovalsListBody.presentation.test.tsx \
  src/components/approvals/__tests__/ApprovalsListBody.emptyState.test.tsx \
  src/components/approvals/__tests__/ApprovalsPage.emptyState.test.tsx
```

Result: **4 files, 8 tests passed**.

## Browser evidence

The running local application was opened at `/approvals` as the Administrator.
The empty policy queue reported `0 items`, `All clear`, and the clear
`No pending approvals` message. The page retained the primary shell, navigation
to approvals, workspace scope, and chat workspace without an error or stale
pending count.

The populated state is asserted by the component browser test above. A
full live multi-user approval flow with a persisted changed-record receipt
remains part of WP-07 completion work.

## Remaining WP-07 work

- Exercise live approval creation, decision, receipt linkage, reload, and
  failure/retry outcomes in the browser.
- Qualify cross-user changes, conflicts, permission revocation, reconnect, and
  deep links against persisted data.
- Complete end-to-end assertions for effective definitions across forms,
  navigation, views, activity, and independent App operation.
