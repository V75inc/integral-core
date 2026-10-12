# Access-control milestone handoff

The user requested a tested push followed by a pause on 11 October 2026. All work remains on `codex/access-control-review`; no merge, release or deployment is authorized. The primary checkout remains separate.

This milestone builds on `5d0212a2` and implements workspace suspension with reviewed resumption, source-specific link grants, absolute resource blocks, consent-based ownership transfer, guarded departure/rehome/cascade, flat access groups and namespaced App function grants. Service execution retains the original actor and intersects current issuer rights. Generic Entry commands enforce protected fields and current identity/workspace admission. Deferred approval replay binds the current policy, exact payload, App definition and resolved targets. Approval expiration and decisions take ordered storage locks. Entry deletion retains post-commit file/embedding cleanup intent. The owner UI exposes suspension, groups, function roles, ownership consent and effective resource access.

The local-installation recovery path now quarantines restored data, rotates session/control authority while preserving encryption keys, and requires a concrete review with a committed primary receipt before serving. Independent signed checkpoint reconciliation is bounded to unchanged canonical resource structure. Missing checkpoints require explicit owner review and credential-disabled recovery. This is a qualified local milestone, not completion of the deployment restore or migration contract.

## Qualification

The machine receipt `milestone_qualification.json` records the required gates, exact assertions, source digest and historical failed attempts. Counts are evidence for this source checkpoint, not closure of all 76 journeys. The shared closure ledger retains its finding-specific rules; no row is closed merely because a suite passed.

Final qualification: 5,676 backend passes /383 explicit skips; 1,346 CI-faithful smoke passes /13 skips; 1,594 frontend passes; Core-only passed; 52 extension passes /300 explicit PostgreSQL-only skips; 51 targeted PostgreSQL passes and a fresh designated DeepSeek cloud allow/deny smoke. The final build/CI and backend targets ran separately after compatibility repairs; all constituent `make verify` gates completed. No test-total-based closure was added.

Compatibility repairs preserve current authority: HTTP Track/account cascades carry the actual caller; direct MCP mounts wire their workspace through the canonical factory; exact connector invocation/read policies retain current structural-owner ceilings. Expired-install compensation rechecks current owner and exact expired state and attributes its audit to the reaper. The opt-in sample consumer now requires `AGENTIVE_SAMPLE_CONSUMER_AGENT_ID` naming an existing rooted facet with a current owner-created policy; opt-in alone grants nothing. Staff discovery admits candidates separately from strict lifecycle inventory, and final canonical authorization still filters every exposed resource. Fresh bulk containment removes repeated authority reads; bounded query budgets retain scaling assertions.

## Continue in this order

1. Finish the remaining R1 output/scope consumers: bind audit/polling lists to request workspace, close actual send/chunk release races, nested-reference disclosure and deleted-resource governance audit; verify admin and public surfaces beyond the qualified Entry/admin paths. Complete the supported writer inventory and current-state migration diff.
2. Finish R2/R3 acceptance edges recorded in `docs/product/ACCESS_CONTROL_LIFECYCLE_HANDOFF.md` and `r3_delegation_evidence.json`: group overlap and leave/rejoin with pending approvals, changed declarations and explicit regrant through App lifecycle, safe integration adoption, and a declared, audited emergency override with separate authority.
3. Finish R4: deployment PostgreSQL restore, continuously current independent checkpoints and structural migration, per-owner recovery credentials, complete paginated owner review and audit UX. Validate browser/API/MCP/native journeys and a clean artifact against every use case.
4. Reconcile the 22 finding and 76 scenario ledgers from named evidence, then run final required gates and the designated real `ollama_chat/deepseek-v4.1-flash:cloud` smoke. Preserve failure receipts and scope qualifications. Push tested milestones to the same branch.

Do not restart the broad audit or interpret this handoff as authorization to bypass failing checks. Do not claim the entire finalized plan is implemented. Current implementation and verification progress remain distinct.
