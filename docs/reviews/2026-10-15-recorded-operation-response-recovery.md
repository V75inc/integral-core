# Recorded operation response recovery

Status: source qualified locally; private coordinator, not deployed.

## Contract

Recovery must distinguish an absent journal, an original completed operation whose
saved output is currently readable, and original completion with withheld output.
Neither absence nor status observation grants execution authority. Only the
ordinary dispatcher can admit a new command; recovery invokes no handler,
selector, target resolver, approval, claim, alias repair or outbox publication.

The exact original principal (including only a currently verified human profile
alias), selected workspace, App, operation key, idempotency key and request hash
bind the lookup. A changed body, incomplete/ambiguous journal, unavailable storage
or invalid current own-history authority remains an error. It never becomes an
absent operation or permission to repeat an effect.

The producer must derive the immutable scope and Subject from authenticated
transport or an admitted immutable run. Constructing an ExecutionScope is not
authentication. Human history admission requires current identity and selected
workspace membership. Services additionally require the direct scoped
`operation.history.read_own` delegation; their issuer is not a receipt alias.

A withheld response contains only the original opaque succeeded receipt
reference, `output_disposition: withheld` and `current_state_verified: false`.
It contains no saved output, references, titles, evidence, principal, key or
current denial reason. An available response still proves past completion,
not current domain state. It must pass original declaration fingerprint,
current function and whole-output dependency authorization. Missing or unusable
legacy output is never re-stamped, repaired or executed again.

## Transaction and release boundaries

The coordinator refuses an ambient Core transaction or a bound jvspatial native
transaction, including wrapped handles. Starting an independent transaction
inside an effect transaction would escape its atomic boundary. Catching an
expected output denial inside a joined transaction can also mark the caller's
transaction failed. Result denial must leave its short read scopes before
falling back to a separately admitted opaque response.

After reading private output, final release repeats own-history admission and
re-observes the exact original receipt under current identity/workspace fences.
Available output also repeats current function/dependency admission within that
interval. Offboarding or journal changes during recovery cannot become a status
success based on an earlier check. No network send or handler runs while these
locks are held. This is an admission boundary, not a claim that already released
bytes can be recalled after revocation.

## Qualification and remaining work

The initial PostgreSQL coordinator run passed 11 cases, covering readable
output, actual App exclusion, removed/changed/read-kind functions, malformed
saved output, storage failure and Core/direct jvspatial transaction refusal.
The initial fixture-bootstrap failure was a local test connection configuration
error, not an application regression. The successful run used the existing
disposable hardening database; no application runtime database was modified.

Expanded native cases add absence, input conflict, incomplete outcomes and
changes between saved-output read and final admission. The final combined
PostgreSQL run passed 103 cases without skips. Full source and CI-faithful smoke
qualification also passed; exact counts, limitations and sealed-source evidence
are recorded in [own-history admission](2026-10-15-operation-own-history-admission.md#final-combined-source-qualification).

This is not yet a public endpoint or SDK/UI integration. HTTP, broker, MCP,
resident and approved-worker producers must establish their own authenticated
binding and invoke recovery before effect transactions. UI consumers must
recognize withheld completion rather than treat it as empty output or mutation
failure. Service result-disclosure paths, transport credentials, independent
concurrent ordering, remote CI and whole-goal executor/restore/load
qualification remain required. Existing App operation execution is unchanged by
this private coordinator.
