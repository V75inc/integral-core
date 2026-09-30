# Core integration merge qualification — 2026-09-30

This record qualifies the reconciled source of PR #96. It does not claim the
broader C6 acceptance matrix or human architecture/Product Owner reviews are
complete. The [integration manifest](pr-integration-sources.json) records the
25 incorporated wave PR heads; commercial PR #91 remains separate.

## Changes and failed first attempt

The candidate restores the missing focused-review/diff gate, modal padding,
reproducible-build and CI image-identity changes, and fixes the current urllib3
and axios audit findings. See the [reconciliation](2026-09-30-pr-integration-reconciliation.md).

On source `9127e7a5a1961521c50609a1b298a22d83555145`, repository, Postgres,
artifact, CI ([36791398118](https://github.com/V75inc/integral-core/actions/runs/36791398118))
and registry ([36791393527](https://github.com/V75inc/integral-core/actions/runs/36791393527))
checks passed. Live GPT-4.1 created `n.Entry.53d614f1514f4f85a96b6d18`
with durable receipt
`o.OperationExecutionReceipt.7147a24ffd739cb7af2baaeaf6f444ac18c91a724d78809c947c2c0c3e0582ed`,
but a generic read was refused and incorrectly represented by dispatch as a
successful zero count. The model repeated that false count. This is retained as
a failed read qualification, not erased by later success.

The correction turns structured route/service read refusals into errors without
rows or counts, preserves the refusal code in a failed broker receipt and supplies
a declared-query repair directive. Successful empty queries are unaffected.

## Browser boundaries

The focused Track Improve-this prefill names the Track and requires draft diff
review before publication. The external App frame completes its handshake and
declared query (its default preview is five rows, not a total count).
The dashboard drill-through modal measures 24 px horizontal and 20 px vertical
body padding, and displays the declared-query refusal clearly. The generic
legacy `chart_region`/`modal_region` saved-view types are not registered in this
packaged Core deployment; their component regression tests are retained,
but this run does not claim live qualification of those legacy plugin types.

## Final qualification

Final frozen-source identity, artifacts and completed gates are recorded below
after the corrected candidate is built and deployed.
