# Approval Experience Plan

**Status:** Proposal for product and architecture review
**Scope:** Agent initiated actions in Integral Core chat, staging, work approvals, and policy-gated actions
**Decision requested:** Approve the interaction and policy direction before implementation

## Executive recommendation

Keep a human decision at consequential boundaries. Remove approval prompts from work that is read-only, private, reversible, or only a proposal. For allowed writes, replace repeated generic “bless” gates with one concise, reviewable action card, and let a clear natural-language approval in the same conversation authorize that exact pending action. Ask again only if the action’s target, material effect, or granted scope changes.

Do not make approval a property of every tool call. Make it a policy decision about the concrete effect the agent proposes. Keep authorization, tenancy, and execution checks in Integral Core; the model may interpret the user's intent, but may not grant itself permission.

## Product comparison

Current official ChatGPT connected-app guidance exposes permission modes such as always ask, allow reads, allow low-risk actions, and (for eligible personal connections) allow all actions. It presents an approval card for an action and permits once-only or bounded future authorization. Higher-risk actions can still require confirmation or be denied by safety and workspace policy ([OpenAI: managing app permissions](https://help.openai.com/en/articles/20001495-managing-app-permissions-in-chatgpt), [OpenAI: apps in ChatGPT](https://help.openai.com/en/articles/11487775-connectors-in-chatgpt)).

Claude's documented tool-approval guidance emphasizes reviewing tool requests and only using persistent “allow always” for trusted tools that should run unattended; its Claude Code CLI also supports explicit allow/deny tool rules and permission modes ([Anthropic: custom connectors and tool approvals](https://support.anthropic.com/en/articles/11175166-about-custom-integrations-using-remote-mcp), [Anthropic: Claude Code CLI permissions](https://docs.anthropic.com/en/docs/claude-code/cli-usage)). These are product-specific controls, not a guarantee that every Claude surface behaves identically.

The common product lesson is **risk- and scope-aware consent**, with a readable action proposal and user choice. Integral should adopt that interaction pattern, while retaining its stricter tenant-aware policy and audit enforcement.

## Current Integral baseline

- The resident-harness document says all harness mutations flow prepare → bless → execute and that batches stage as one card ([RESIDENT_HARNESS.md](RESIDENT_HARNESS.md#4-staging-contract-write-safety)).
- The staging service persists pending changes with token, user, session, workspace, expiry, human and machine diffs, idempotency, execution state, and resolution timestamps. It is therefore a useful authorization record, not merely a UI confirmation.
- The chat card currently offers **Approve**, **Approve & auto-allow**, and **Reject**; large diffs open a review modal. It distinguishes approved from applied and offers undo where supported ([StagedChangeCard.tsx](../../frontend/src/features/ai-chat/staging/StagedChangeCard.tsx), [StagedChangeReviewModal.tsx](../../frontend/src/features/ai-chat/staging/StagedChangeReviewModal.tsx)).
- The Approvals page is a second place to approve pending changes. This is appropriate as an inbox/recovery surface, but should not create another approval step for a change already approved in chat.
- Core already has distinct authorization and approval mechanisms: per-resource policy checks, staged chat writes, durable work approvals, and scheduled-task write scopes. A chat confirmation must not replace policy enforcement or cross those boundaries.
- Recent browser evidence shows the useful target experience is already technically possible: an ordinary user request produced one staged record, “Yes, please go ahead” in the normal composer authorized that pending record, and the assistant read the saved data back. Search and soft-delete flows also completed in chat. The same evidence documents how repeated turns and redundant follow-ups can make CRUD feel laborious ([natural chat CRUD evidence](harness-evidence/natural-chat-crud-2026-10-05/README.md)).
- One material inconsistency: the inline card offers “Approve & auto-allow this kind” while destructive/share/invitation kinds are explicitly excluded from session auto-bless in the staging service. The UI should not offer a control whose applicability varies invisibly by operation.

## Recommendation: one interaction model, four action classes

Classify the **effect**, not the tool name or the user's wording. The policy engine remains the authority and returns an action class plus the required consent level. Tool metadata provides a default classification; Core resolves the actual target, data, and risk at runtime.

| Action class | Examples | Expected behavior |
|---|---|---|
| **Read / explain** | Search entries, inspect a track, summarize an attachment already provided, answer from authorized workspace data | Proceed without an approval dialog. Enforce normal workspace and resource permissions. Ask a concise question only if needed to identify the requested resource or resolve genuine ambiguity. |
| **Private, reversible, low impact** | Save a private draft, create a record in a confirmed app/track, edit a reversible field, organize private workspace data | Normally proceed when the user clearly requested the action and the configured autonomy policy permits it. Otherwise stage one compact card; accept a clear “yes / do it / file it there” reply as approval of that exact pending proposal. Read back the result. |
| **Material or external effect** | Send/publish a message, invite/share with another person, update an external connector, change access, expose sensitive data, run a multi-record operation | Show one just-in-time card with destination, audience, records/data affected, and consequences. Require explicit approval of that exact effect. Natural language may approve only if the pending proposal is unambiguous and unchanged; never infer broad or future permission. |
| **Destructive, financial, security-critical, or hard to reverse** | Permanent deletion, payment/purchase, role/security changes, bulk delete, irreversible external action | Require a strong explicit confirmation at the point of action, with a clear summary and a targeted confirmation control. No session auto-allow. Where the risk is disallowed by policy, deny rather than asking the user to override. |

Defaults should be conservative for deployed tenants, but not needlessly interrupt users: permit reads; permit eligible low-risk reversible writes only under user/tenant settings; ask for material external effects; require strong confirmation for destructive/high-impact effects. Workspace policy may narrow these defaults and may prohibit classes entirely.

### What approval must not mean

- A proposed design or answer is not itself a write approval. Present a setup preview once; ask for missing information only when needed. When the user asked to build and the preview is within their request and policy, do not show a second redundant “confirm this design” prompt before presenting an identical approval card.
- A read is not a write. Never put search, inspection, or verification behind a write approval card.
- A model saying “approved” is not authorization. Only explicit user intent, a valid scoped grant, or an authorized policy principal can authorize execution.
- Approval does not confer permission. Each operation still checks the actor's current workspace/resource access and policy immediately before execution.
- One approval does not authorize an unbounded plan. It authorizes the specific reviewed effects described by the proposal.

## The chat experience

1. **Do the useful work first.** Resolve the workspace and likely app/track using Integral's capability search and authorized reads. If the destination is clear, do not ask which skill/tool to use. Clarify only unresolved user-facing ambiguity.
2. **Show the result/proposal in context.** For ordinary actions use a compact inline card in the transcript, not a blocking modal. State in plain language what will change and where; expose changed values and affected-item count. Include an expandable details view for large/batched changes.
3. **Use one decision point.** Primary action: “Approve” or a verb that names the effect (e.g., “Add 3 records”). Secondary: “Edit” (return to chat with proposal context) and “No, leave it unchanged.” Do not repeat a separate design confirmation when the staged proposal already captures the reviewed design.
4. **Accept natural approval against the pending action.** If one eligible pending action exists and the user replies clearly in the conversation, resolve that pending proposal directly. If there are several, ask which one; never map an ambiguous “yes” to all. Honor natural cancellation/refusal and show that no change was made. Treat the explicit card action and conversational approval as the same decision API.
5. **Keep the transcript honest.** Show “Applying…” only after the executor starts; distinguish approved, applied, failed, and partially applied. On success, summarize the completed effect and verify it with an authorized read. On failure, state what did and did not happen and provide recovery/retry guidance.
6. **Make the Approvals surface an inbox, not another hurdle.** It lists pending decisions across conversations, shows source conversation and consequence, and performs the same approve/reject decision as the inline card. Once decided, every surface reconciles to that decision. No extra dialog after approval unless the action class requires strong confirmation.

## Autonomy and repeated consent

Replace the generic “Approve & auto-allow this kind” button with deliberate, understandable permission settings. Use three scopes:

- **This action** — one pending proposal; default for material actions.
- **This conversation** — optional short-lived permission for a named low-risk action class, exact app/track boundary, and bounded record/operation count. Show expiry and revoke control. No destructive, external, sharing, security, or financial actions.
- **Workspace policy** — administrator-configured defaults and allowlists; separate from a chat click and visible to workspace admins.

Any grant must bind to principal, workspace, capability/action class, resource boundary, limits, expiry, and policy version. Do not grant by vague “kind” alone. A grant must not be transferable across workspace, user, session, target app/track, or action class. Re-evaluate live permissions at execution. If the proposed effect exceeds the grant or a material fact changes, stage a new decision.

For V1, keep the existing one-shot approval and do not expose broader autonomy until its scope and audit semantics are consistently enforced. The UI's present session auto-allow should be hidden for any action not demonstrably eligible; the eventual autonomy preference belongs in an explicit, inspectable permission control.

## Batch and ambiguity rules

- Group operations into one approval only when they form one coherent user-requested outcome, share a target/scope, and can be reviewed together. Show exact counts and representative or expandable per-record effects.
- Never combine unrelated destinations or risk classes into one “approve all”. Split the batch or request clarification.
- Prefer atomic application. When atomicity is impossible, disclose partial-failure behavior before approval, execute with per-operation idempotency, preserve a progress receipt, and report exactly which items succeeded or failed. Retrying must continue/reconcile; it must not duplicate completed writes.
- Resolve “that receipt” or “the new tool” from conversation context only when a unique authorized target exists. If multiple apps/records remain plausible, ask one focused question and preserve the attachment/action context while waiting.
- An expired, revoked, superseded, or policy-stale proposal cannot be approved. Regenerate a current proposal with a new idempotency key and visible changed details.

## Core contract and implementation seams

Keep UI wording and modal presentation outside the policy/staging authority. Define a stable Core decision contract so the harness provider can evolve without reimplementing consent:

1. **Effect classification:** tool binding declares its default effect class, reversibility, externality, and batch behavior. Core may elevate risk based on resolved arguments/data; a model cannot downgrade it.
2. **Decision object:** immutable proposal ID, principal, workspace, conversation/session, resolved target, normalized effect summary, exact diff/affected count, effect class, policy decision, idempotency key, expiry, and policy version.
3. **Decision path:** button click and model-selected natural-language decision both call the same Core API with a server-held proposal reference. Natural-language interpretation may select among proposals but cannot manufacture a token or alter a proposal.
4. **Execution path:** re-resolve tenant/resource permissions and policy; atomically claim execution; execute idempotently; record per-effect receipt and final outcome; reconcile all UI surfaces. Approval is not execution success.
5. **Audit:** record who/what authorized, explicit versus conversational channel, exact proposal/version, scope/grant used, policy result, execution outcome, and timestamps. Do not persist hidden chain-of-thought. Preserve privacy and tenant isolation in logs and analytics.
6. **Shared adapter:** one presentation contract for chat, Approvals inbox, and supported external channels. Channel-specific confirmation strength may be stricter; a plain-text approval from another channel must prove principal/session binding and resolve to one pending proposal.

This builds on the current server-held staging token, policy engine, durable work approval, and idempotency machinery. It should not add a second approval framework or let the harness library become the authority.

## Rollout plan

### Phase 0 — inventory and policy matrix

- Enumerate every agent-capable tool/binding and every existing stage, `requires_human_approval`, work approval, routine `write_scope`, and direct-write path.
- Assign effect class, reversibility, externality, and execution semantics with a named owner for exceptions.
- Reconcile documentation and UI behavior, especially session auto-allow exclusions and tools that stage even when the user asked for a completed action.
- Deliverable: reviewed action matrix and migration map; no policy behavior changes in this phase.

### Phase 1 — unify decision semantics

- Specify a typed approval decision/proposal contract over current staging primitives.
- Ensure card approval, natural-language approval, Approvals inbox, and supported channel adapters call one scoped decision service.
- Preserve binding to tenant, principal, conversation, target, exact diff, policy version, idempotency, expiry, and live reauthorization.
- Remove any generic approval copy that suggests a design preview and a staged write are two separate confirmations for the same effect.

### Phase 2 — remove needless interruptions

- Keep read-only paths prompt-free.
- Show one inline approval card for actions needing consent, using plain impact-focused wording and expandable details.
- Support conversational approval/refusal when exactly one valid proposal is pending; ask a short disambiguation question when multiple proposals exist.
- Combine coherent batches into one card and improve partial outcome/progress display.
- Keep Approvals inbox and chat cards reconciled to the same decision state.

### Phase 3 — risk tiers and preferences

- Add effective user/workspace settings for reads, eligible low-risk reversible actions, and always-ask classes; administrator restrictions win.
- Initially ship session grants disabled. Enable only after policy and audit contract tests cover target/action binding, revocation, expiry, and cross-tenant denial.
- Prohibit auto-approval for irreversible, external-send/share, financial, security, sensitive-data disclosure, and policy-denied effects.

### Phase 4 — qualify real user journeys

Run browser smoke tests with lay-user prompts and no skill/tool vocabulary. Use a disposable workspace and verify browser readback and receipts after every write. Cover: read/search with no approval; create/update with one approval; natural “yes” and natural “no”; ambiguous destination clarification; setup preview followed by one approval; coherent multi-record batch; external/share action; destructive action; duplicate/retry; permission change while waiting; expiry; partial failure and recovery; cancellation; attachment interpretation and filing; Approvals inbox approval; and tenant isolation. Repeat a representative subset on each supported model family because the model interprets intent while Core must enforce the same decision semantics.

## Acceptance criteria

- Read-only user requests produce no approval card and still obey resource permissions.
- A clear user-directed, eligible write has at most one consent decision; setup/design preview is not a duplicate approval.
- A natural approval or refusal applies only to the single server-held, current proposal in the bound principal/workspace/conversation. Ambiguity never approves multiple proposals.
- High-impact and external actions receive the required explicit/strong confirmation or are denied by policy.
- No model output can bypass approval, widen its scope, mutate the pending proposal, or bypass execution-time authorization.
- Each approved effect has an idempotent execution receipt and verified user-facing outcome; retries cannot duplicate completed effects.
- Chat and Approvals inbox show the same state without requiring a second decision.
- All decisions and outcomes are auditable, tenant-scoped, and do not store hidden chain-of-thought.
- Browser qualification records lay-user prompts, visible approval count, time-to-result, successful readback, errors/recovery, model/provider, and exact workspace scope.

## Open product decision

Before implementing Phase 3, decide whether Integral V1 may automatically execute low-risk reversible writes after an explicit user request, or whether it should initially keep one compact inline approval for every mutation while removing prompts for reads and redundant confirmations. Recommendation: ship the compact one-shot approval as the default first, with a policy architecture that supports low-risk auto-execution, then enable that autonomy only after policy tiers and browser qualification prove the boundaries. This limits rollout risk without cementing “approval for every tool call” as the design.
