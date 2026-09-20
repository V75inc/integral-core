# Integral agent experience: remediation plan

Date: 2026-09-19. Status: proposed implementation plan; no runtime fixes claimed here.

## Objective and scope

A user describes an operational need, reviews a comprehensible design, authorizes it, and receives a usable application. The same agent can subsequently change records, answer questions, add useful views and dashboards, evolve schemas, and recover interrupted work. Its explanations must agree with persisted state and the UI.

The unit of remediation is Integral's generic delivery and operation system: resident skills, public tools, substrate services, approval continuation, and rendered projections. The rental app is one regression fixture. No domain-specific branches, prompts, fields, routes, or dashboard exceptions belong in Core.

This plan deepens C5 in `2026-09-19-core-finish-line.md` and depends on its operation and lifecycle contracts. It does not replace the distribution, independent extension, or release qualification work.

## Evidence and limits

The preceding fresh-database browser run exercised ordinary resident requests, followed approvals, and compared the result with rendered state. Current source inspection corroborates contract gaps; it does not establish every root cause.

| Observation | System implication | Evidence status |
| --- | --- | --- |
| Named table displayed generic columns; named status board displayed stock workflow columns and an unassigned record | View existence is insufficient; configuration must resolve to typed profile fields and reach the renderer | Browser reproduced; exact loss point requires tracing |
| Record update saved number/date but changed generic entry status while the app's Status field remained unchanged | Field identity is ambiguous across schema, tools, and views | Browser reproduced, including approval before/after |
| Query repeated the generic-status interpretation | Read and write tools can agree with each other while contradicting the user's app semantics | Browser reproduced; query arguments need trace capture |
| Dashboard persisted but status grouping used platform status and deadline list included unrelated record types | Data-source validation and execution need semantic parity | Browser reproduced; service source explicitly groups top-level status and silently falls back to it for unknown grouping |
| Required schema fields appeared in the form, but the requested existing-record update was abandoned | Schema publication, backfill, and record editing need one maintained delivery obligation | Partial success reproduced; approval labelled add-entry-type does not itself prove the backend used the wrong mutation |
| Dashboard and schema continuation produced duplicate approvals; cancelling a duplicate caused inaccurate narration | Resume needs authoritative effect receipts and logical-operation identity | Browser reproduced; trigger and ownership of duplication remain to be isolated |
| Relations rendered correctly and calendar followed the updated service date | Preserve working substrate paths while tightening contracts | Positive browser evidence; all document-date mappings and reminder execution remain unproven |

Current implementation anchors:

- Resident skills: `agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/skills/`.
- Public tool definitions/dispatch: `backend/app/agentive/tool_manifest.yaml`, `tooling/bindings.py`, `tooling/dispatch.py`.
- Approval execution: `backend/app/agentive/staging.py`, `staging_executors.py`; frontend `features/ai-chat/staging/` and `prompt-sheet/`.
- View contracts: `backend/app/views/contracts/` and frontend view registry/components. The table/kanban/calendar JSON files inspected advertise availability but do not specify configuration schemas.
- Dashboard schemas, validation and resolution: `backend/app/schemas/dashboards.py`, `services/dashboard_widget_validation.py`, `services/dashboard_service.py`. Validation currently checks widget type and line-chart grouping; selected data resolvers do not consume the declared arbitrary `filters` property.
- Schema lifecycle: `services/agent_profile_patches.py`, profile draft/publish and migration services. Preserve I-PROFILE-02: use the existing patch and publish/reject-gate path.

## Target experience

1. **Understand:** establish workspace, existing app context, intended outcomes, and only genuinely missing decisions.
2. **Design:** maintain a versioned blueprint mapping every requested outcome to schema, relation, operation, projection, or routine and its acceptance check. State capability limits before promising delivery.
3. **Authorize:** present one coherent change and impact summary. Bind authorization to that revision, scope, and effects. Preserve chat confirmation for greenfield delivery; do not add redundant technical approvals.
4. **Apply:** execute dependencies through existing governed services; record durable per-step results. Changes to scope or materially different effects require a revised proposal, not reuse of stale authorization.
5. **Verify:** read persisted state independently, evaluate representative operations, and check rendered field bindings. Distinguish applied, verified, partial, failed, and cancelled.
6. **Continue:** carry unfinished obligations across skill changes, approvals, refreshes, and restarts. Finish dependent record updates after a schema revision. A completed effect remains completed if a later step is cancelled.

## Ordered work packages

### R0 — Establish reproducible acceptance evidence

Ownership: acceptance tests and diagnostic fixtures, not product-specific implementation.

- Capture sanitized request, selected skills, tool inputs/results, proposal revision, approval IDs, effect receipts, readbacks, and browser assertions for the observed failures.
- Convert each failure into a deterministic regression using public tool dispatch and real services. Keep model selection out of the deterministic tests.
- Build a reusable expected-outcome manifest independent of the agent's generated claims. Include decoy records/tracks and fields with names colliding with system properties.
- Record candidate commit plus working-tree/artifact identity and initial fixture state. Keep evidence isolated from developer data.

Exit: each observed defect has a reproducible assertion and a positive control; the overall journey cannot be green merely because an App exists or tools returned no errors.

### R1 — Make field and projection contracts authoritative

Ownership: schema introspection, view contracts, tool parameter schemas, dashboard data-source validation, shared query/mutation semantics.

- Define explicit references distinguishing platform attributes from profile fields, resolved against track, entry type, and schema revision. Reuse existing references where possible; document a compatibility adapter for older saved configs.
- Publish machine-readable configuration schemas for table columns, board grouping/columns, calendar mappings, and dashboard data sources from the canonical registries. Generate frontend and tool-facing descriptions from that source.
- Validate referenced fields, types, enum values, relation targets, and track scope before staging and again against the revision at execution.
- Implement supported profile-field filtering/grouping, date windows, null semantics and exact count behavior through a shared service path. Define inclusive boundaries, timezone, pagination and multi-date OR semantics; a due-soon predicate must not silently become a created-at predicate.
- Reject unsupported config keys or grouping/filter expressions with an actionable error. Eliminate silent fallback to generic status and silently ignored predicates.

Exit: UI views, dashboard widgets and agent queries return the same expected record set/count under identical typed predicates; collisions with `status`, `title`, and `date` never change the wrong property. Tests include empty sets and data beyond one page.

### R2 — Repair approvals, effect receipts, and continuation

Ownership: existing staging/dispatch, prompt-sheet continuation, and durable work services.

- Trace the duplicate-approval reproduction before modifying ownership. Establish one authoritative operation state shared by backend, agent continuation, and UI.
- Carry a structured effect receipt containing logical operation ID, authorized revision, outcome, affected IDs, resulting revisions and verification status. Resume consumes that receipt instead of inferring success from a generic 'approved' message.
- Bind idempotency to the logical operation and authorized payload. Repeated delivery, double-clicks, response loss, or agent retries return the prior result rather than staging/executing a duplicate. Deliberate later identical edits remain possible as new operations.
- Persist unfinished plans and dependency progress using the existing work kernel; reconcile unknown outcomes before retry. Do not create a second workflow engine.
- Preserve completed effects on partial failure/cancellation. Present the remaining work and a safe continuation action. Recheck permissions and stale revisions at apply time.

Exit: approve → resume → refresh/restart yields one effect and no duplicate prompt; a cancelled later step cannot produce 'nothing was created' when earlier work persisted. Revoked access and changed payloads cannot reuse authorization.

### R3 — Complete record operation and schema evolution workflows

Ownership: entry tools/services, profile patch/draft/publish, migration integration, and associated skill handoffs.

- Ground mutations in the current entry type and exact field keys; use minimal validated patches. Return persisted values and independently read back requested properties.
- Route modifications of existing fields/types through explicit revision operations. Clarify whether add-type is insert or upsert and prevent accidental replacement of sibling fields or views.
- Treat 'add fields and populate existing records' as one dependent plan: inspect population → show schema/data impact → authorize → publish/migrate/backfill safely → validate records and projections.
- Required-field additions need a valid backfill/default or a clearly presented unresolved migration condition. Preserve existing customization, records, relationships, IDs and unaffected view bindings. Respect the migration reject gate; never silently force publication.
- Continue the requested record update after publication without requiring the user to repeat the request or manually complete it. Use existing transactional and durable recovery guarantees; do not claim cross-service atomicity that has not been demonstrated.

Exit: additive fields, rename/type-change failures, populated required fields, stale edits and interrupted publication have explicit tested outcomes; dependent edits finish or remain visibly recoverable with no false completion.

### R4 — Re-author the system skill complement around delivery ownership

Ownership: resident system skills and their tool allowlists/examples; no customer-app hardcoding.

| Skill | Responsibility after remediation |
| --- | --- |
| `integral_scaffold` | Own the blueprint, dependency plan, capability coverage, build and final verification |
| `integral_model` / `integral_profiles` | Inspect and revise attached schemas; distinguish library lifecycle; preserve migration and customization semantics |
| `integral_entries` | Resolve typed fields, perform record operations, maintain relations, and verify saved values |
| `integral_insights` | Execute grounded queries with explicit scope, predicates, completeness and date semantics |
| `integral_dashboards` | Compose validated widget queries and verify result sets/counts against source records |
| `integral_scheduling` | Create and verify actual routines, ownership, timing, notification destination and lifecycle; distinguish scheduling from successful delivery |
| `integral_review` / navigation helpers | Inspect delivery evidence and open the correct usable surfaces |

- Remove contradictory examples and prose-only workarounds; ensure every recommended configuration exists in the live contract and every required operation is available to that skill.
- Keep one owner for compound requests while specialist skills supply instructions. Persist unfinished requirements across handoffs.
- Make verification obligations explicit: required table columns, board field and options, every promised calendar date mapping, linked records, real routines and operation availability.
- Distinguish automated invariants from agent-guided procedures. Never promise state synchronization or scheduled notifications solely because a field or SOP exists.
- Use concise progress messages grounded in receipts: proposed, awaiting authorization, applying, verifying, complete, or partial with recovery. Separate 'saved' from 'verified'.

Exit: skill compliance and example-to-contract checks pass; complete journeys work without corrective coaching or manual rescue. Unsupported requests lead to useful scoped alternatives, not invented tools or silent omission.

### R5 — Qualify the complete experience across domains and failures

Ownership: backend/frontend regression suites, browser acceptance, live-model evaluation and release evidence.

- Exercise at least four structurally different fixtures: equipment checkout, client/project delivery, stock/replenishment, and the existing rental case. Vary labels, field keys, enum values, mixed types, relations and date mappings.
- Run the full sequence: need → clarification/design correction → build → inspect views/records → add dashboard → update → query → alter schema/backfill → query/render again.
- Include multiple workspaces, denied access, no matches, nulls, date boundaries, duplicate requests, stale revisions, provider interruption, restart, partial cancellation and capability limits.
- Verify reminders by triggering due work with a controlled clock and observing one persisted notification, including replay/restart. Verify promised record-state transitions through their actual implementation.
- Separate deterministic release gates from live-model reliability. Initial live qualification: five independently seeded runs per domain on each supported configuration, plus recovery/correction cases. Require zero silent incorrect writes, false success, duplicate effects or scope violations; require at least 95% complete unassisted journeys. This is a bounded acceptance sample, not a statistical claim of infallibility.
- Report requirement coverage, intervention rate, tool failures/retries, latency and token/cost distribution. Set supported budgets from measured baselines rather than inventing them.
- Run repository gates (`make verify`, Core-only, contract and applicable Postgres lanes), fresh-install browser tests, and public MCP/resident parity against the same build. Existing xfails are explicit debt, never release proof.

Exit: a reproducible evidence manifest identifies tested artifacts, supported configurations, passed scenarios and remaining limitations. Successful recovery is acceptable; silent corruption or fictitious completion is not.

## Sequence and completion decision

### Conversation-flow acceptance supplement

The [chat-log review](2026-09-19-chat-flow-review.md) records CF-01–CF-12 with exact turn excerpts, priorities, remedies and acceptance criteria. These are required additions to R0–R5: receipt-grounded language, no repeated approvals, precise cancellation scope, maintained dependent tasks, evidence-qualified verification claims, compact system-event rendering, and semantic approval summaries. Conversation quality must be evaluated alongside saved data and browser behavior; a correct write accompanied by contradictory or misleading guidance is not a complete experience pass.

Start R0 immediately. Implement R1 and R2 foundations next, then R3. Revise R4 against those executable contracts, not speculative APIs. Run R5 incrementally throughout and as the final gate. Each implementation package should land with its own regression evidence and remain reviewable.

The experience is ready when users can complete and evolve multiple unfamiliar applications through the ordinary resident flow, with data and rendered projections agreeing, no hidden manual repair, and recoverable failures. Application-specific cosmetic repair, repeated prompt coaching, and broader model context are not substitutes for these exit criteria.

Preserve the jvspatial object-spatial contract, graph reachability, public extension boundary, scoped permission enforcement, declarative profile patching, and existing migration reject gates. This document proposes work only; it does not certify the current runtime or authorize publication.
