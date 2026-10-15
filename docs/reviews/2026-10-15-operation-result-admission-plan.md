# Operation output admission follow-through

Date: 2026-10-15. Status: native defect reproduced; implementation pending.
This is the remaining declared-output/privacy boundary from the Core hardening
review, not a new operation execution journal or an App-specific feature.

## Current evidence

`services/app_operations/result_access.py::authorize_replayed_result` checks
only three top-level `object_refs` collections, returning early when all are
empty. Native probes using actual authenticated owner/member identities and
current Entry exclusions show it accepts a nested unreadable Entry reference
and adjacent label both with and without a valid visible top-level reference.
Evidence: `/tmp/core-operation-nested-result-red.xml` (two expected assertion
failures). Probe source: `/tmp/test_core_operation_nested_result_postgres.py`.
This tests the admission seam; no particular App endpoint exploit is claimed.

The dispatcher validates `output_schema` before `build_result`, but returns raw
handler output alongside canonical references. `build_result` currently fetches
all App Tracks for reference ownership checks. Replay has two release sites:
read-cache hits and completed command receipt replay. Initial result construction
is a third relevant boundary. The existing `OperationIdentity` and transactional
receipt already own command execution and must remain authoritative.

## Required implementation

1. Use one bounded resource-dependency admission contract for initial output,
   read-cache replay and command-receipt replay. Preserve current schema
   validation and owning-App restrictions on mutation `object_refs`.
2. Inspect supported typed `ObjectRef` shapes and canonical resource references
   inside nested output, including arrays and neighboring labels. Bound values,
   nesting, strings, aggregate text and unique authority lookups. Missing,
   malformed, unsupported or incompletely inspected references cannot silently
   become an empty dependency set.
3. Resolve current canonical resource containment rather than trusting saved
   parent/workspace caches. Keep reads within the selected workspace and current
   resource policy; any cross-App source needs its own current read authority.
   Replace the unbounded App Track inventory with per-reference canonical
   location checks.
4. Admit under the current principal/workspace release boundary. Preserve actual
   human/agent/connector subject policy rather than converting function authority
   or human delegation into unrestricted record-read authority. Recheck current
   function/declaration authority at release as well as record dependencies.
5. Withhold the whole output when a dependency is denied, gone, foreign or
   unavailable; censoring only an identifier can leave a disclosing label.
   Distinguish unavailable authority from a verified denial internally. A saved
   receipt remains proof of the earlier effect and never permits blind replay.
6. Reuse the existing operation receipt and exact key/input identity. Review
   current output schema on replay where the declaration has changed; an old
   cached response is not a new execution of the replacement definition.
7. Keep the contract generic. Opaque domain identifiers and unstructured prose
   are not a natural-language privacy classifier; resource claims must use
   supported canonical/typed contracts. Do not add domain field-name heuristics.

## Acceptance evidence to retain

- Native ordinary-member allow/deny of nested typed and canonical references,
  including no top-level refs, visible top-level refs, foreign workspace,
  missing/deleted resources, competing or cache-forged containment, and current
  grant/removal after the operation has already committed.
- Initial response, read-cache replay and mutating command replay exercise the
  same projection rules. Denied replay preserves the saved effect/receipt and
  does not call the handler again. Initial refusal preserves correct local
  transaction rollback semantics and honest external outcome classification.
- Human, delegated agent and connector subjects retain their own policy limits;
  current function revocation and nested record revocation are independent.
- Bounds reject excessive/cyclic/non-serializable payloads; malformed top-level
  reference collections do not bypass validation by being falsey. Canonical
  selection remains bounded without fetching the App's complete Track set.
- Separate PostgreSQL connections qualify admission ordering with identity,
  workspace and resource revocation. A proof covers admitted payloads, not the
  impossible recall of bytes already released to a transport.
- Focused tests, whole-source gates, CI-faithful selection and public artifact
  checks follow the final implementation. Existing green unrelated suites do
  not close this newly reproduced output boundary.

## Related cleanup

`services/app_operations/execution_receipts.py` still says the dispatcher is not
wired to the primitive, although `dispatch.py` now uses `execute_operation_once`
and event outboxes. Correct that stale introductory comment in this increment;
do not rewrite the receipt's stable identity or persisted request fingerprint
as documentation cleanup.
