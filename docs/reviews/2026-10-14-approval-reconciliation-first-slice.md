# Approval reconciliation: first implementation slice

Date: 14 October 2026. Status: local implementation after published checkpoint `ac34fcba`; not committed, pushed or deployed.

## Implemented

- A successful executor followed by a `StagingError` in completion or design-receipt persistence now retains `application_state: reconciliation_required` in the outstanding proposal's existing `last_error` record.
- The response clearly reports completion/reconciliation failure and preserves its underlying code. It no longer clears that explanation with the normal success path.
- The same proposal cannot invoke its executor again while that marker exists. Normal refused/unapplied writes remain retryable.
- Tests inject consumption and design-receipt failures, verify one executor invocation, reload the outstanding proposal through the durable store, and verify that another principal is refused.

## Qualification

The staging application selection passed on JSON and PostgreSQL. Configured formatting, lint, convention and backend typing checks pass after formatting the added tests. The final JSON selection also includes existing chat work/approval routes; its retained log is `/private/tmp/integral-browser-latency/approval-reconciliation-final.log`.

## Remaining boundary

This deliberately does not claim to close R1/R2. The legacy staging store still persists best-effort. If its marker cannot be stored, a restart can lose that protection; a crash before the marker is recorded is another unresolved window. Independent workers still need a shared atomic execution claim. Non-StagingError/cancellation paths and partial batch effects still need durable intent/result reconciliation. No manual-clear or automatic-reexecute recovery endpoint is added here.

Next: a stable proposal command identity, shared fenced execution claim, and command result receipt committed with local graph effects. Recovery must reconcile a known result rather than replay it, and must withhold unknown non-transactional outcomes pending reviewed recovery. These changes need independent-worker concurrency, post-commit failure and crash/restart tests before a durability claim.
