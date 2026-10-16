# PR 136 PostgreSQL CI repair

The PostgreSQL job in run 38618995414 failed six tests on d970b28a.
The backend, frontend and independent artifact lanes passed.

- Comment receipt and restart tests now provide a scoped test-only encryption
  key, including the restart child process. Production encryption requirements
  and installation keys are unchanged.
- The cross-process batch test checks the two applied encrypted step receipts
  and verifies one App, Track and Entry after resume. Legacy progress hints and
  the outer batch-dispatch call are no longer its authority/evidence.
- The revision test exercises the typed App-operation ledger through real
  staged decisions, with explicit execution workspace identity. Canonical Entry
  receipt integration is covered by the dedicated staging creation/apply tests.
- The expiry expectation uses the current classified `expired` error and still
  asserts no further effect dispatch. Revision refusal, correction, consume,
  cancellation and persisted receipt checks are retained.

The complete PostgreSQL spike and contract selections passed locally without a
global encryption-key override (`TESTING=1`, `DEBUG=false`). The full-native
contract run includes the repaired restart/revision and comment cases. The slow
event-history probe was pruned and the local backup/restore drill skipped when
pg_dump could not reach the disposable host port; restore remains unqualified.
Configured format/lint/type hooks and staged guards passed.

The private-default visibility amendment is a separate source change and is
being qualified by its own full backend and CI-style smoke gates.
