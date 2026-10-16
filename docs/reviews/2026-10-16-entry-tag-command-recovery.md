# Entry-tag approval recovery qualification

## Boundary and implementation

Qualification started from `7e16cda1` plus the native-design approval
follow-through. After PR #142 merged, the tag-only commit is based on
`22d381a3`; its runtime source is unchanged. It routes both entry-tag endpoints through
one generic delta service. It does not change App-specific behavior, add a new
execution authority, or bypass human approval. The original approval remains an
exact `add_entry_tag` or `remove_entry_tag` command, not a replacement tag list.

The PostgreSQL command joins the canonical entry transaction, current identity
and workspace admission, Entry row serialization, and existing typed update
validation/revision/relationship reconciliation. It checks the Tag's persisted
scope and canonical attached-model containment before mutation. Graph change,
revision event intent and exact-command receipt commit together. Event delivery
is offered only after the service's own root transaction commits; callers that
own an outer transaction leave delivery to the existing recovery sweep.

Receipt observation returns current authorized Entry state. It never reapplies
the saved delta after a later opposite edit. The local JSON store retains its
single-process semantics; it does not gain distributed rollback guarantees.

## Reproduction

The unmodified endpoints failed four native cases: each delta kind lacked an
exact applied receipt after lost completion, and receipt-storage failure did not
prevent its effect. The corrected red run reached the actual staged dispatcher
and PostgreSQL effect boundary. Sequential `Node.connect` already deduplicated
edges; ordinary sequential edge duplication was not the reproduced defect.

## Qualification evidence

The existing tag CRUD, build-tag, retrieval-hook and Entry CRUD selection passed
33 tests without skips, errors or failures. The initial expanded native selection
passed 10 tests, including two-pool distinct-delta concurrency, lost completion,
receipt fault rollback, later opposite mutation, foreign taxonomy refusal and
post-commit event offering. An additional selection including App-level Tags and
revoked membership passed 13 tests without skips, errors or failures.

The final expanded native selection passed **15/15**, with zero skips, failures
or errors (`/tmp/core-entry-tags-qualified-final.xml`). It adds simultaneous
attempts at one approval and detached taxonomy containment. A completed proposal
remains terminal (`already_consumed`); a losing concurrent caller may receive
`already_executing`. Neither refusal repeats the mutation. Separate
pools in one process do not prove independent-process restart behavior: the
same-approval case also exercises Core's in-process staging guard. Existing
cross-process receipt/fence contracts must be reconciled with the final source.
A supplementary separate-interpreter test passed **1/1** (15 deselected), with
two workers paused at a common start boundary. It retained one applied receipt
and increased the Entry revision exactly once. External event delivery was
explicitly deferred in these subprocesses; the real transactional outbox intent
remained enabled. This is effect/claim proof, not external-delivery qualification.

Configured pinned formatting, lint, typing and convention checks and all staged
substrate guards passed on this source. The combined full backend subsequently passed **6,593 tests / 866 skipped**,
zero failures/errors (7,459 collected, 4,485.163 seconds;
`/tmp/core-design-tags-combined-full.xml`). CI-faithful passed **1,797 / 22 skipped**
(`/tmp/core-design-tags-combined-ci.xml`). The combined native selection passed
**34/34** (`/tmp/core-design-tags-combined-native.xml`): 16 tag cases, 15 design
cases and three release admission cases. These counts overlap the component
runs and must not be added to them.

All four independent artifact gates passed. The reproducible Core wheel SHA256
was `db14df11a3d357169c6b129deef962bf5438b3e23ad11548c065ea9a7a8d1408`;
logs: `/tmp/core-design-tags-artifacts.log`. The sealed source manifest
`/tmp/core-design-tags-qualified-source.sha256` matched before parent alignment;
all changed runtime/test files remain identical after alignment. Documentation
was reconciled to preserve the already-merged design suite result. No deployment
or runtime-data change was performed.

## Remaining scope

These two commands do not close all approval families, mixed batches, external
side effects, immutable decision history or the broader hardening ledger. Tag
mutation and removal still obey current schema and taxonomy constraints; recovery
cannot infer an unknown effect from a missing receipt or restore a removed Tag.
