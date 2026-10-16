# Approved App operation recovery boundary

Source audit: 2026-10-15, candidate based on `5fd97b8d`. This records the next
required integration; it is not an implementation or qualification claim.

## Two different approval owners

| Producer | Immutable binding and effect owner | Recovery requirement |
| --- | --- | --- |
| Resident staged `invoke_app_operation` | `app_operation_staging.prepare_agent_app_operation` persists the human principal/workspace, original `input`, resolved input, `operation_idempotency_key` and reviewed `declaration_fingerprint`. `_x_invoke_app_operation` calls canonical dispatch; staging consumption follows its commit. | Reconcile lost staging completion against that exact canonical operation receipt, before scope/handler preflight can refuse old output. Never use staging progress or a returned success string as proof. |
| Policy Approval for a declared function | `app_operation_approval` persists the original Subject, policy, complete App binding, original input and key. `approval_executor._decide` holds current identity/workspace/approval facts; `approved_effect_scope` joins effect, decision and event outboxes in one graph transaction. | Retain this existing transaction owner and original Subject. Do not add a second staging journal or perform the new outside-transaction recovery lookup inside its held effect scope. Terminal decision observation and current output disclosure are different contracts. |
| HTTP extension operation | Authenticated request principal plus validated selected workspace and client intent key. The new coordinator runs before snapshot/broker admission. | Currently implemented in the isolated consumer candidate; backend source/transport qualification and reconciliation remain distinct. |
| Broker/MCP/resident direct invocation | The immutable AgentRun and invocation carry principal/workspace, snapshot, key and source. Current snapshot/RunStep checks precede the adapter. | Establish the original Subject and admitted run binding before status observation. Do not trust payload subject fields, replace the original actor with an issuer, or bypass ordinary fresh execution admission on journal absence. |

`claim_execution` on staged changes is an in-process guard. For command families
outside the canonical receipt inventory it is not proof of shared worker
serialization or atomic completion. The existing App operation receipt already
serializes its own effect; another competing effect journal would not solve
staging consumption loss.

## Additional evidence needed before consuming a recovered proposal

The private operation observation currently identifies principal/workspace/App,
operation key, exact idempotency key and request hash. Canonical dispatch hashes
the original selector input, not the resolved input. The staged payload retains
both; recovery must use the original `input` and preserve the original resolved
review binding for execution.

A new proposal can have a different reviewed declaration while a caller reuses
an old operation key and input. Past operation completion alone therefore does
not prove that this particular reviewed declaration executed. The operation's
original declaration fingerprint is currently inside its saved private result
(`_operation_contract`), not an independent journal field. Withheld-output
recovery must not decode private output merely to pretend this missing approval
binding is available.

Before automatically consuming a recovered staged App proposal, introduce and
qualify immutable, separately readable effect-contract metadata on the original
canonical operation receipt, or another exact existing authoritative binding.
The staged proposal's reviewed declaration must match it. Do not backfill such
metadata by guessing from a current manifest, silently re-stamp an old receipt,
or create an alias/claim as an observation side effect. Legacy receipt proof
that is insufficient leaves the proposal in explicit reconciliation state;
known past operation status does not authorize replay or a fresh approval.

## Required acceptance matrix

- Actual staged approval executes one effect and commits its canonical receipt;
  injected staging-consumption failure then recovers after fresh process/cache
  reconstruction without resolving a handler or repeating an effect.
- Changed/removed current function and revoked result access may withhold output;
  current human identity, workspace and exact approval binding still govern the
  reconciliation. An opaque result must not become a fresh success narrative.
- Same key/input with a different reviewed declaration cannot consume the new
  proposal, even though the old operation is known to have completed.
- Legacy missing contract metadata, changed input, incomplete/ambiguous receipt,
  journal outage and original Subject mismatch never become fresh execution.
- Competing workers, stale claim owners, revoked/expired/terminal proposal
  decisions and cross-workspace hints retain their existing decision boundaries.
- Policy Approval closure remains atomic with its effect and outboxes. Do not
  move execution outside that transaction to satisfy the new coordinator's
  outside-transaction requirement.

No remaining executor family is closed by this audit. The authoritative inventory
still lists 44 non-native kinds and the conditional batch boundary separately.
