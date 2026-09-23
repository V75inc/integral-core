# Integral resident chat: flow review and remedies

Date: 2026-09-19. Status: observed findings and proposed remedies; no implementation claimed.

Companion to [the system remediation plan](2026-09-19-agent-experience-remediation.md). Scope is the general resident experience, not a rental-specific assistant.

## Evidence reviewed

Reviewed the persisted conversation rendered in the local Integral browser, from the initial operational request through design, build, dashboard creation, record update, query and schema evolution. Expanded the schema-request and both schema-continuation tool groups. Compared claims with the preceding browser smoke-test observations. Inspected frontend `features/ai-chat/prompt-sheet/resumeDisplay.ts`, which explicitly parses and displays the synthetic 'Please continue.' footer.

Conversation locator: Core Acceptance personal workspace; conversation beginning 'I need an app to manage my car rental business'; app `n.WorkspaceApp.f05f817e9b29487a896f80e7`. This review uses turn sequence and exact excerpts as locators, not invented timestamps or message IDs. Only selected tool groups were expanded; this is not a complete raw provider trace audit. Token figures below are UI labels, not independently reconciled billing or context measurements.

## Findings

| ID / priority | Chat evidence | Experience defect | Remedy and acceptance |
| --- | --- | --- | --- |
| CF-01 / P1 | Initial design lists fields and views, then repeats much of them under Acceptance Checklist | The user reads the same implementation inventory twice, while meaningful operating decisions remain implicit | Present a compact outcome-oriented design with essential decisions and assumptions. Keep the detailed checklist inspectable. Test that clarification requests concern unresolved consequential choices rather than re-asking supplied information. |
| CF-02 / P0 | 'Cars track does not duplicate rental state; status is updated via Rentals'; later 'Automated reminders' | Design prose promises operating behavior without explaining its mechanism, boundaries, timing, or owner | Record each promise as a blueprint obligation with implementation and evidence. Distinguish enforced automation from agent-guided procedures. Establish reminder schedule/destination using stated defaults or necessary clarification. No automation claim without an actual verified implementation; reminder delivery remains unproven in this run. |
| CF-03 / P0 | 'Your Car Rental Management app is ready!' despite incorrect table and board projections | Existence checks are presented as complete delivery | Completion requires requirement-level verification. Report exactly what works and what remains unresolved; finish repair within the authorized plan. A fixture with a present but misbound view must not receive a ready verdict. |
| CF-04 / P0 | 'The Toyota Camry entry has been updated' followed in the same answer by 'These changes are now staged and will be saved once approved' | One response describes incompatible execution states | Generate state wording from the authoritative receipt. Pending means proposed, approved means authorized, applied means saved, verified means independently checked. Assert that pending/rejected/failed operations never receive completed-action language. |
| CF-05 / P0 | Dashboard approved → 'Dashboard ... created' → duplicate pending creation; schema approved → 'Once this schema change is approved' | Continuation repeats the completed step and asks again | Resume from logical-operation results and remaining dependencies. Expanded schema trace confirms `integral_modify_model` was called in the initial request and again as the sole tool in the approval-resume turn. Isolate why it re-staged; enforce replay reconciliation without suppressing legitimate later edits. One authorized revision must yield one effective operation and no duplicate approval. |
| CF-06 / P0 | After cancelling the duplicate dashboard prompt: 'no dashboard has been added yet' | Cancellation of pending work erases awareness of a completed effect | Scope cancellation to the precise pending operation. Reconcile actual state and preserve completed receipts. The regression must leave the existing dashboard acknowledged and distinguish any cancelled additional proposal. |
| CF-07 / P0 | After record approval: 'These are the exact saved values'; subsequent query lists Toyota as Maintenance while the app field remained Available | Confidence exceeds evidence, and the query propagates the wrong semantic interpretation | Require schema-aware readback of the requested field, not an echo of proposed arguments or generic status. Link results to source records and include date boundaries when needed for interpretation. Test with colliding system/custom field names. |
| CF-08 / P0 | Final schema response says 'new fields appear in forms and tables'; its expanded tool group contains only `integral_get_track_schema` | Schema existence is falsely presented as rendered-view verification | Track evidence type: schema inspected, data read back, binding checked, UI observed. A schema-only read cannot justify a table-rendering claim. Test exact required columns and form fields independently; UI observation is not required for every live turn, but unsupported visual claims are prohibited. |
| CF-09 / P0 | Promises to update rate/currency, then says 'please use the entry edit form' and 'If you need me to ... let me know' | The agent abandons an explicit dependent task and transfers work back without a concrete blocker | Keep the record update in the durable obligation list. After schema success, continue through the entries capability and verify. If cancellation makes remaining intent ambiguous, state precisely what was cancelled and seek only necessary clarification; do not silently discard the obligation or infer fresh authority. Test ordinary approval continuation and partial cancellation separately. |
| CF-10 / P1 | Repeated system notes: 'Prompt resolved', 'Approved ...', 'Please continue.'; repeated widget/field lists around these notes | Internal control turns inflate the conversation and obscure actual progress | Keep resume metadata as structured system events. Render a compact operation status attached to the proposal, with details available. Advance automatically when authorized; omit the synthetic imperative from normal presentation. Preserve accessible announcements and the audit history. |
| CF-11 / P1 | Approval says 'Add entry type Car on Cars' for a request to add two fields; undo says 'No recorded changes for this approval' although fields appear | Approval and history language do not explain the user's actual change or recovery options | Derive the card from the semantic diff: added fields, requiredness, affected records, preserved views and migration implications. Explain undo availability accurately. Do not equate the add-type label with proven backend failure; the form did gain the fields. |
| CF-12 / P2 | UI labels show 76.1k tokens for design, 257.5k for build and 171.9k for a three-property update; repeated offers to help end already requested work | Potential context/cost overhead, technical clutter, and low-value closure | Audit token accounting, skill/tool payload size and repeated schema reads. Cache only with correct schema revision, principal and scope invalidation. Keep model/token diagnostics behind details by default; retain useful progress. Measure before assigning a cause or claiming savings. Remove generic help offers that substitute for completion. |

## Corrected conversational contract

Every active request has an intent, scope, approved revision, completed effects, outstanding obligations, evidence and next action. These are shared execution state, not merely instructions for the model to remember.

- **Before authorization:** 'The proposed change adds two required fields and fills them on the existing record. Review the impact below.' The card explains actual effects once.
- **During execution:** 'The schema change is saved. I am updating the existing record and checking the result.' Only emit this after the schema receipt confirms it.
- **After verified completion:** 'Both fields are saved on the record. The existing views are preserved.' Mention table visibility only if its binding or rendering has actually been checked.
- **On partial completion:** identify the saved effects, the failed or cancelled step, and the concrete recovery action. Do not recast the entire request as successful or cancelled.
- **On unsupported behavior:** explain the specific capability limit and supported alternative before making a promise. Do not claim automation because an SOP exists.

The assistant should narrate meaningful changes and the next dependency, rather than replaying the whole proposal after each approval. An empty result should state the applied scope/date interpretation when relevant and must come from a successful complete query. A failure or incomplete scan is not 'None found'.

## Implementation integration

These findings extend the existing R0–R5 packages rather than introducing a separate chat engine.

1. **R0:** preserve these turn excerpts and selected tool sequences as transcript regression fixtures. Pair each claim with independently expected state and evidence. Add cancellation and failed-query counterexamples.
2. **R1/R3:** fix semantic targeting and return enough validated readback for CF-02/07/08/11. A wording patch must not disguise incorrect data.
3. **R2:** fix duplicate resume, replay/cancellation state and durable unfinished obligations for CF-04/05/06/09. Reuse the existing staging/work kernel and audit model.
4. **R4:** revise skill ownership, clarification, completion language, promise tracking and concise progress for CF-01/02/03/04/08/09/12. Check tool availability at each handoff; avoid manual fallback merely because the current skill lacks the operation.
5. **R4 frontend:** revise prompt-sheet status rendering, diff-based approval summaries and diagnostic disclosure for CF-10/11/12. Keep accessibility and inspectable history.
6. **R5:** evaluate conversation quality alongside database and browser assertions. The flow must succeed without testers correcting the agent, cancelling duplicate prompts, or manually editing data to manufacture a pass.

## Conversation acceptance gates

Hard failures: unsupported completion or automation claims; contradictions with saved state; duplicate approvals for the same resolved operation; lost dependent tasks; claimed verification without corresponding evidence; scope leakage; failed queries represented as empty results.

Measure separately: unnecessary clarifications, repeated proposal content, user interventions, approval count per coherent change, unassisted task completion, latency, token usage, and recovery steps. Establish baseline values before setting performance thresholds. Do not compress away information the user needs to approve a material change.

Required replay scenarios: approve once and resume; refresh after apply; response lost after apply; reject before apply; cancel only a remaining step; schema succeeds but backfill fails; permission revoked between proposal and execution; schema-only read versus rendered verification; legitimate identical later request. Pair model-independent lifecycle tests with live-model transcript evaluation and manual review of ambiguous cases.

## Review corrections and limits

The earlier smoke commentary initially inferred a misrouted schema operation from its approval label. The later form inspection proved the required fields did materialize. The defensible findings are confusing operation naming, repeated staging, insufficient verification evidence and abandoned dependent work; the exact mutation defect still needs service-level tracing.

The ordinary design confirmation was appropriate. Approval visibility and detailed diagnostics are useful when presented clearly. This plan removes redundancy and false state claims while preserving meaningful user control and auditability. No live application data was changed for this review.
