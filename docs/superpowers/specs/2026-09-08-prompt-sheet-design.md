# Prompt Sheet — Unified Agent↔User Sequester Dialog

**Date:** 2026-09-08
**Status:** Implemented
**Repo:** Integral

## Problem

Integral has two agent→user gates today:

1. **Write bless** — `StagedChange` cards inline in the assistant bubble (`StagedChangeCard` / `InlineStagedCards`).
2. **Clarifying questions** — `integral_ask_user` → inline `UserQuestionCard`.

Neither hard-halts the model. Ask-user returns immediately and relies on soft “STOP” guidance (`chat_threads.py`). Inline widgets are buggy and easy to lose in the transcript. Multiple prompts do not page as one sequestered interaction.

Cursor/Claude equivalents: Claude Code `AskUserQuestion` (blocking overlay + multi-question paging) and separate tool-permission gates. Integral needs a product equivalent that fits chat + staging.

## Goals

- One **bottom sheet** over the chat composer for all in-thread prompts (questions + write bless).
- **Halt further tool use** until the queue drains or the user cancels.
- Agent has a clear tool (`integral_ask_user`) for prompting the user; write proposes enqueue into the same queue.
- Multiple items **page in order**; Prev/Next to browse; Skip only on questions.
- **Cancel all** is the only dismiss-without-finishing path; agent receives a cancel summary.
- Keep write apply semantics: Approve still runs `bless_and_execute` immediately; agent still waits for queue close.

## Non-goals

- Policy `Approval` nodes / Approvals page rewrite (out-of-band stays).
- True harness turn-suspend / deferred tool-result futures (jvagent interact park).
- Cross-channel SMS/Slack text-approve redesign (may feed the same queue later).
- Dimming the transcript under the sheet.

## Decisions (locked)

| Topic | Choice |
|---|---|
| Scope | Unify questions + write bless (C) |
| Placement | Bottom sheet over composer; no transcript dim |
| Resume | After queue drains (or Cancel all) |
| Skip | Questions only; writes must Approve/Reject |
| Cancel all | Revoke pending writes; cancel open questions; keep already-approved in this sheet |
| Halt mechanism | Durable `prompt_queue` on `ChatThread` + dispatch tool gate |

## Architecture

```
Agent tool (ask_user | propose-write / commit_batch)
  → enqueue PromptItem on ChatThread.prompt_queue (opens if closed)
  → tool result returns envelope
  → further tools BLOCKED while queue.status == open
  → FE PromptSheet pages items; composer locked
  → drain (all resolved) OR Cancel all
  → queue.status = closed
  → resume turn with resolution summary
  → unlock composer
```

### Data model

Add durable queue state on `ChatThread` (alongside / superseding single `pending_question`):

```text
prompt_queue: {
  status: "open" | "closed",
  opened_at: iso,
  closed_at: iso | null,
  close_reason: "drained" | "cancelled" | null,
  items: PromptItem[]
}
```

`PromptItem`:

- Common: `id`, `kind` (`question` | `staged_write`), `status` (`pending` | `answered` | `skipped` | `approved` | `rejected` | `cancelled`), `created_at`, `resolved_at`
- `question`: `question`, `header?`, `options[]`, `allow_other?`, `multi_select?`, `answer?`
- `staged_write`: `token`, `kind`, `summary`, `diff_*` refs (same shape as today’s staged_change envelope)

Migration: replace `ChatThread.pending_question` with `prompt_queue` as source of truth. Readers/writers that checked `pending_question` move to “open queue has unresolved `question` items.” No dual-write period.

### Dispatch gate

In `agentive/tooling/dispatch.py`:

- On `integral_ask_user`: enqueue one item per entry in `questions[]` (1..N); open queue.
- On propose / `integral_commit_batch` (session-bound): enqueue `staged_write` after `create_staged_change`. Session-autonomy auto-blessed tokens are **not** enqueued.
- While `prompt_queue.status == open`: **block propose/execute tools** (and any
  unknown tool). **Read tools stay available** so the model can resolve the next
  named target (list tracks/apps, read schema) before it stops for approval.
  Multi-part requests (delete on track A, seed track B) otherwise cannot discover
  B until resume, then inherit A's UI focus. Return a structured
  `prompt_queue_open` error for blocked calls. `integral_propose_design` remains
  exempt so a mid-flight amend can replace a pending design card.
- Gate key = chat session / thread id. No session: keep today’s `session_required` for ask_user; writes without thread stay Approvals-inbox only (no sheet sequester).

### Resume

On drain or Cancel all, close the queue and deliver one structured resume summary by appending a synthetic user turn (same pattern as today’s “Approved — please proceed.” nudge), e.g.:

```text
User resolved prompt queue (drained|cancelled):
- q1: answered "Strict"
- q2: skipped
- write <token>: approved (applied)
- write <token>: revoked (cancel all)
Continue from here.
```

## Agent tool surface

- Keep name: `integral_ask_user`.
- Manifest + agent role: use for any clarifying / branching user input while in Integral chat; do not use for write confirmation (staging cards / sheet write pages already gate writes).
- Payload: support `questions[]` (ordered). Optional `allow_other` per question.
- After enqueue, model must stop tooling; gate enforces that even if ignored.

## Frontend

### `PromptSheet`

- Anchored above / replacing the composer strip in [`Thread.tsx`](frontend/src/features/ai-chat/components/Thread.tsx).
- Shows one item; header `n / N`; Prev/Next.
- Question page: option buttons, Other input, Skip.
- Write page: reuse staging card actions (Approve / Auto-allow / Reject / review modal). No Skip.
- Footer: **Cancel all**.
- Composer locked while queue open (no send).
- Transcript remains fully visible (no dim overlay).
- Optional transcript chip: “N prompts pending” — not the primary control.

### Retirement

- Remove primary reliance on inline `UserQuestionCard` and `InlineStagedCards` for in-thread sequester.
- Keep Approvals page (`StagedChatApprovals`) for out-of-band / no-session tokens.

### APIs (FE)

- `GET` open queue for thread
- Resolve question (answer / skip)
- Bless / reject write (existing staging endpoints + mark queue item)
- `POST` cancel-all
- Drain detection can be server-side after each resolve (close + return resume payload when last item resolves)

## UX rules

1. Answering / skipping / approving / rejecting resolves the current item and advances to the next unresolved item.
2. Prev/Next only navigates; does not resolve.
3. Queue drains when every item is resolved (`answered` | `skipped` | `approved` | `rejected`).
4. Cancel all: revoke still-pending write tokens; mark open questions `cancelled`; leave already-approved items as-is; close with `close_reason=cancelled`; resume with cancel summary.
5. Refresh mid-sheet: reload open queue; reopen sheet; keep composer locked.
6. Reopening after drain/cancel starts a **fresh** `items` list — prior resolved
   items must not stack into the next sheet (resume already summarized them).

## Testing

- Enqueue question + write; next tool call blocked by gate.
- Drain produces resume summary; composer unlocks.
- Cancel all revokes pending, keeps approved, informs agent.
- Skip allowed on questions; rejected/absent on writes.
- Refresh restores open queue.
- FE: paging, composer lock, no transcript dim.
- Session auto-bless: no sheet item for auto-blessed mint.

## Key files

| Area | Files |
|---|---|
| Model | `backend/app/models/nodes.py` (`ChatThread`) |
| Queue service | `backend/app/services/chat_threads.py` (extend; replace `pending_question`-only path) |
| Dispatch | `backend/app/agentive/tooling/dispatch.py` |
| Staging | `backend/app/agentive/staging.py`, `api/staging.py` |
| Questions API | `backend/app/agentive/api/questions.py` → generalize to queue APIs |
| Manifest | `backend/app/agentive/tool_manifest.yaml` |
| FE sheet | new under `frontend/src/features/ai-chat/` |
| Thread / composer | `frontend/src/features/ai-chat/components/Thread.tsx` |
| Staging cards | `frontend/src/features/ai-chat/staging/*` (reuse actions inside sheet) |

## Open follow-ups (explicitly deferred)

- Feeding cross-channel text-approve into the same queue.
- Collapsed transcript history of resolved sheet sessions.
- Partial true turn-suspend if gate-alone proves insufficient under streaming races.
