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

Frozen executable source: `bb3b1e0b11bc80db697d7187dc9b7e2212789dc1`. Subsequent ledger-only commits do
not change these binaries. Exact identities are in [the JSON record](c6-merge-qualification.json).

| Gate | Result and retained evidence |
| --- | --- |
| Repository | `make verify` passed; 1277 frontend tests; complete backend lane, guards, types and CI-faithful smoke. `refusal-verify.log`. |
| Postgres | `make test-postgres` passed against fresh worker databases on port 15434; 12 explicit skips. `refusal-postgres-corrected.log`. An initial run used the wrong default port and was stopped; it is not a pass. |
| Core / contracts | `make verify-core-only verify-contract` passed. `refusal-contracts.log`. |
| Independent artifacts | Core, SDK, clean install and signed external App lanes passed. `merge-independent-artifacts.log`. Retained Core wheel ASGI import passed in a fresh environment with explicit development/test posture; the initial unsafe test posture was correctly refused. |
| CI | [36793137333](https://github.com/V75inc/integral-core/actions/runs/36793137333), success on the frozen SHA. |
| Registry / deployment | [36793131441](https://github.com/V75inc/integral-core/actions/runs/36793131441), success; fresh-runner GHCR deployment ready. Local Harbor API/web config IDs were removed, verified absent, pulled by digest, and deployed healthy. Cached layers may remain locally; independent runner proof is separate. |
| Browser | Focused Track prompt; external frame handshake/query; declared registration; reload and eight-row Inventory; dashboard modal spacing and visible access refusal. |
| Transport | HTTP/MCP declared queries return identical eight rows, object references and workspace scope. Unauthenticated MCP returns 401. Both command transports replay the UI operation's durable receipt without a duplicate asset. `merge-parity.json`, `merge-operation-replay.json`. |
| Resident | Generic-read refusal now has failed receipt and repair directive; GPT-4.1 follows it and reports the successful declared query's seven rows before UI registration. Declared write remains unqualified; details below. |
| Restore | Populated fixture restored into scratch: 140 nodes, 174 edges, 303 objects, 2 embeddings; counts and identity match; scratch removed. `merge-restore.log`. File-volume recovery remains separate. |
| Human reviews | Architecture and Product Owner review remain pending. Source merge is not release acceptance. |

Logs and screenshots are retained under `.qualification-evidence/` in the
qualification worktree. Wheels are retained at `/private/tmp/integral-c6-merge-artifacts/dist`.
The unchanged signed external App archive has SHA-256
`a0de55cad63d0fcbc099da06c4f7ef0a4917fcfbd746d8b555ed3ae0d8431cdf`.
No source tree or SDK mount is present in the deployment; only the extracted App,
its verification key and managed data volume are mounted. Docker installs from
package metadata; the deployed versions are recorded separately from `uv.lock`.

## Resident limitation and merge boundary

The first corrected turn automatically repaired the refused generic read and
reported seven real assets. Its operation discovery did not complete registration.
Two follow-ups still did not execute the declared operation; a final continuation
proposed a generic `create_entry` approval. That proposal was rejected. It is not
a successful declared-write proof and is not counted as first-pass completion.
No further live-model retries were used to mask this result.

The final asset was instead registered through the installed App's declared
operation form: `n.Entry.86f8e772da2d4aedac38742f`, category `other` (form default),
with receipt `o.OperationExecutionReceipt.fbd74451eefadfa5f4ec71359b5123c8356455985788fe07054ce314992bb25c`.
HTTP/MCP replay and eight-row readback prove that actual effect.

The recommendation is to merge the reconciled platform source and close its
verified overlapping source PRs, while retaining this resident write qualification
and the broader C6 acceptance/human review gaps. The merge does not publish a
production release or close C6. PR #91 remains outside this integration.
