# Access-control lifecycle milestone handoff

11 October 2026. Source frozen at the user's request for a qualified milestone commit on `codex/access-control-review`. This is a continuation record, not a claim that the entire access-control delivery plan is complete. The root coordinator owns final qualification, commit and push. No worker commits or pushes were made.

## Implemented boundaries

Ownership transfer uses rooted consent proposals, explicit accept/decline and revision checks; the old immediate transfer services refuse consent bypass. Accepted ownership/history/proposal changes share the PostgreSQL transaction. Source-specific share grants retain independent provenance and source revocation preserves overlapping direct grants. Suspensions block identified re-entry; resumption requires an exact current reviewed-rights digest and does not recreate deleted rights. Absolute resource blocks protect the canonical workspace owner.

Workspace unlink refuses nonempty implicit migration. Explicit rehome fences both current workspaces and reconciles current membership/grants, resource caches and paused destination containment. Group/action migrations, installed/source execution sidecars and unsupported cross-boundary references remain explicit conflicts; there is no automatic complete migration of those subsystems.

Owner deletion traverses the complete rooted subtree, retains valid shared operational-model roots, rejects external model consumers and propagates failures. Named cascade permits carry the actual actor; empty/system actor shortcuts do not authorize destruction.

Deferred approvals now recheck current approver and original subject/delegator, exact originating policy/action/resource and normalized full mutation intent. Supported built-in writers create canonical rooted artifacts with the real delegator as owner/author and original agent as audit actor. Claims, effects, linked work and actual mutation/decision outboxes commit together. Expiration and approval hold the same exact scalar approval row before terminal transition; this is necessary because the pinned adapter's full JSON predicate fallback itself performs an unlocked read followed by save. Namespaced App approvals were subsequently integrated by the delegation worker.

## Local restore implementation

`local_runtime.restore` installs a quarantine marker, rotates JWT/control authority and preserves encryption keys. Runtime start/supervisor and the coordinator's independent API boot gate reject unreviewed restoration. Database-only maintenance runs without API, harness, scheduler or public listener.

`integral checkpoint --checkpoint-directory …` retains a private signed checkpoint plus latest generation anchor outside the installation/backup. `recovery-review` exposes a redacted current-versus-restored authority report and exact digest. `recovery-apply` validates the digest in the same primary transaction as rights reconciliation and a durable recovery receipt. Current replay requires the same canonical resource/containment structure. Structural/schema drift requires the explicit owner-reviewed fallback; it is not silently replayed.

Without an independent checkpoint, recovery retains only explicitly reviewed existing canonical workspace owners, resets their credentials, disables other identities, removes ordinary membership/resource/group/source grants, disables deferred subjects/actions, pauses Apps/Tracks, revokes inbound stored sessions/bearers and retains encrypted recovery data. Restored mailbox proof is cleared. Owner recovery currently resets all selected owners to one operator-supplied recovery password; per-owner secure credential delivery is outstanding.

New files: `backend/app/services/access_recovery.py`, `backend/app/recovery_maintenance.py`, `backend/tests/test_access_recovery.py`, `backend/tests/contract/test_access_recovery_postgres.py`. Modified recovery integration: `backend/app/local_runtime.py`, `backend/app/cli.py`, `backend/tests/test_local_runtime.py`. The coordinator owns `backend/app/main.py` boot-gate integration.

## Qualification available at freeze

- Coordinator real PostgreSQL lifecycle/delegation run: 18 passing cases, including consent, source races/rollback, suspension race and rehome/audit rollback. Earlier individual fixture/containment failures were repaired.
- Coordinator real PostgreSQL approved-effects run: 14 passing built-in/linked-work cases before later namespace integration; subsequent isolated expiration cases passed. The final integrated frozen run belongs to the coordinator.
- Worker preliminary JSON observer/writer run: terminal 0, 41 cases. Local runtime recovery run: terminal 0, 40 cases. Independent signed-anchor/quarantine run: terminal 0, 2 cases.
- Recovery PostgreSQL file collects three cases: current revoked-grant reconciliation/receipt; stale-review denial and after-persist receipt failure rollback; explicit owner fallback with old ordinary membership/grant removal. No worker PostgreSQL run was started. Final result must be recorded by the coordinator.
- New recovery runtime files compile and pass the backend-configured flake8 check. Full frozen repository gates, clean artifact, browser/API/MCP acceptance and DeepSeek 4.1 Flash Cloud smoke are coordinator obligations.

## Remaining obligations

The local restore path does not yet gate `scripts/pg_restore.sh` or implement deployment/operator restore reconciliation. That script was not edited. Independent checkpoint currency depends on the operator retaining the latest signed external anchor; checkpoints are manual and are not yet advanced by every authority mutation. A stale-but-latest retained manual checkpoint must not be described as current revocation proof. Establish a continuously maintained current checkpoint, or require the credentials-disabled fallback when currency cannot be established.

The current authority classifier must be reviewed against every executable/security entity and field, including future types. Full raw checkpoint collection prevents omitted evidence, but the replay projection still uses known authority classes; unknown future authority cannot automatically be claimed closed. Structural/schema drift replay, per-owner recovery credential delivery, deployment restoration, complete migration of unsupported rehome subsystems and cross-surface release acceptance remain open. No deployment qualification is implied.

## Expired-install compatibility gate repair

The legacy reaper's unconditional `actor_id="system"` could no longer pass canonical cascade admission. The bounded repair compensates only an unchanged, expired `awaiting_settings` App with install provenance and no submitted settings, under its original current owner's identity and workspace fence. Exact ownership and containment are rechecked; normal cascade permissions still apply. The final audit names `system/app_install_reaper` and retains the authority owner's ID; its outbox joins the compensation transaction.

An inactive or unavailable original owner leaves the pending App intact for retry/review; the timer acquires no substitute authority. Focused JSON qualification passed exact purge/system audit, nonexpired retention and active-state zero-effects (three cases). PostgreSQL compensation races are not covered by that local receipt. The final frozen repository gate remains the coordinator's responsibility.
