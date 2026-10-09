# Build field identity and native runtime qualification — 2026-10-09

## Problems

The approved Work Tracker design recorded Projects → Target date as `due_date`, while the apply created it as `target_date`. Plan fidelity accepted an identical display label despite the different key; the immutable receipt retained the approved key, so verification incorrectly reported the existing field as gone.

During local deployment, an older Docker stack accepted traffic on wildcard ports 4000/9006 while the native runtime could still bind their IPv4 loopback addresses. Requests intermittently reached the older installation, causing invalid-credential responses, expired sessions, and incompatible frontend chunks. A successful bind alone did not prove that the port was available.

## Changes and boundaries

- Reject a planned field whose key differs from the approved key before executing writes.
- Version new execution receipts as schema version 2. Their field lookup remains exact.
- For original, unversioned receipts only, permit a single matching display label within the recorded Track and EntryType, only when that label is also unique in the approved schema. Existing type, relation, and choice checks still apply, and alias recovery also requires matching requiredness. Exact-key lookup takes priority, and ambiguous or incompatible aliases remain unverified.
- Report `legacy_field_key_alias`, the approved key, and the resolved key in the verification item. This recovery is read-only; it does not rewrite approvals, receipts, fields, or view bindings.
- Probe an existing loopback listener before selecting a preferred runtime port and avoid reusable bind probes. Occupied ports fall back to an OS-selected port, which is retained in the installation contract.

Authorization, workspace scope, original design revision/digest, receipt ownership, and denied/read-failed outcomes retain their existing enforcement. Recovery does not identify fields outside the receipt's recorded Track/type and does not apply to new receipts.

## Local and browser evidence

Source base: `fabca0c7234d94f1654ab60f21fbf15e3a51aa42`, the current remote `codex/pr-113-staging` at qualification time. The local wheel was built with `.ci/bundle_resident_harness.sh` and includes this change and was packaged with a fresh frontend production build. The installed runtime source, wheel source, desktop bundled wheel, and frontend index were checked against the checkout/build.

Wheel: `integral_core-0.1.1rc16-py3-none-any.whl`; SHA-256 `2f05a71f13aa7ad7a28eca60c0e6cf97b8f7330a0594ae0aeeab82a8fc4ba56f`.

The upgraded native installation selected API port 56543 and web port 56545, avoiding the existing Docker listeners. The desktop restarted and signed in using its existing account. No credentials or permissions were reset.

The in-app browser repeated `integral_verify_build` checks through the existing DeepSeek v4.1 Flash conversation, including after the final complete-wheel deployment and desktop restart. The checks returned `verified`, with all 25 blueprint items `present`. Only the read-only verification tool was used on each turn. The original design revision and execution receipt were retained; the Projects date item reported `due_date` → `target_date` with explicit alias metadata.

The restarted desktop rendered the Projects table with one Target date column, the Project deadlines calendar, and a new-project form with one Target date input. The form was cancelled without saving. Both Tracks remained empty; the original routines and their UTC schedules were retained. This qualifies receipt recovery and empty-state rendering, not a scheduled digest firing or arbitrary future app builds.

Local screenshots, original tool results, deployment backups and test logs are under `/tmp/integral-business-field-identity-fix-20261009/` and are not shipped in Core.

## Automated checks

Targeted design, verification and runtime tests passed (76 cases). Coverage includes new-receipt exact lookup, legacy alias recovery, wrong types, exact-key precedence, ambiguous live labels, duplicate approved labels, invalid fields without keys, requiredness mismatches, deleted fields and occupied-port detection. Port regressions also carry the smoke marker.

Frontend suite: 277 files, 1,578 tests passed. Guards, formatter checks, mypy and pre-commit passed. The final deployed wheel passed `.ci/verify_artifact_baseline.sh`, including its resident skill inventory. The CI-faithful smoke run passed. The full backend suite completed with expected integration skips and two nested-test command failures: the system `pytest` lacked xdist and rejected the inherited `-n` option. Both affected policy-audit checks passed when rerun with `backend/.venv/bin` on PATH and empty `PYTEST_ADDOPTS`. No source changes or test exclusions were needed. The initial `make verify` command therefore exited nonzero; all remaining stages passed and the two failed checks passed on the corrected-environment rerun.
