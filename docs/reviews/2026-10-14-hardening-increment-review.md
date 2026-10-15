# Hardening increment review

Scope: current uncommitted PostgreSQL staging, event history, scope and stream
changes in the hardening checkout. This is an incremental source review, not
independent artifact, CI or deployment acceptance. The GSD phase workflow could
not run because this checkout has no phase state; source review proceeded
inline. No independent reviewer was spawned.

## Fixed during review

- Decision-store variable redefinition blocked the configured backend type gate.
- Malformed and timezone-free approval expiries lacked a classified fail-closed
  response. Regression cases now cover those records.
- Linked WorkApproval rejection preceded the authoritative staging rejection.
  An already-applied change could therefore retain its graph effect while its
  linked approval became rejected. Link updates now follow successful rejection;
  the real post-commit race test verifies no false rejection is sent.

## Open findings

1. **Expiry cleanup ordering implemented; broader recovery remains open.**
   Native sweeps, cold reads and terminal deletion now use current shared decision
   and effect evidence. Tests cover pending expiry, stale deadlines, active/lost
   owners, committed results, unknown external dispatch and partial batches.
   Durable downstream ledger delivery and replacement/partial repair still need
   qualification before distributed recovery can close.
2. **High: native receipt migration remains incomplete.**
   App/Track creation, complete inline Track setup, Entry create/update and
   mixed creation/Entry batches now share receipts. Other mutation families
   retain earlier execution/recovery semantics. Library side effects and the
   remaining commands require their own semantic qualification.
3. **High: extension I/O still occurs inside canonical Entry transactions.**
   Persisted dispatch intent prevents blind replay but does not remove long
   transaction holds or establish a safe extension effect contract.
4. **Qualification: stream release checks are fresh but not ordered against all
   concurrent revocations.** Define and test the admission boundary without
   holding a database lock while awaiting the network.

The full outstanding ledger is in `2026-10-14-core-hardening-execution.md`.
