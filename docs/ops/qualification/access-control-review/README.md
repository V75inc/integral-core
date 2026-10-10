# Access-control review evidence

Date: 10 October 2026. Baseline: `9d13b44e`.

[Assessment, 21 findings and 76 use cases](../../../product/ACCESS_CONTROL_REVIEW.md). [Implementation plan](../../../product/ACCESS_CONTROL_PLAN.md). [Machine-readable results](results.json).

## Results and interpretation

The original assessment passed 290 existing targeted tests across three disjoint selections. The second pass retained that historical evidence, ran 13 additional existing regressions, and reproduced 23 observations with the expanded characterization probes. The original 290 were not rerun in this second pass. Those probes intentionally assert the observed current behavior, including defects and policy mismatches. Their passing result is evidence that the findings can be reproduced; it is **not** evidence that Integral meets the proposed security requirements.

An additional attempted generated-document service probe could not import the legacy module because its dependency references missing `HAS_TEMPLATE_VERSION`. AC-19 therefore records the source pattern and import limitation, and is excluded from the reproduced observations. The final retained characterization runner passed all 23 cases in 10.57 seconds; the additional existing regressions passed 13 cases in 8.91 seconds.

This review changed no runtime source. Unrelated App/UI edits were present and preserved during the recheck; `9d13b44e` identifies HEAD, not a clean tested tree. The probes were run temporarily beneath `backend/tests/` to use the existing conftest fixtures, then preserved here outside normal pytest test discovery. They include synthetic graph records, authenticated in-process HTTP requests, warm-cache probes, ordered race/fault injection and mock policy rows. The common service fixture now catalogs its Users, Workspace, App and Track using the canonical graph helpers, which makes its containment realistic. The dedicated missing-anchor probe still deliberately constructs corrupt state. These are disposable tests, not recommended graph creation patterns.

The existing tests used the checkout's backend virtual environment, `TESTING=1` fixtures and default JsonDB storage. A generated temporary Fernet key supplied test-only credential configuration. The tests used isolated per-test fixture stores; no production database or real identity-provider/mail delivery was qualified. The general suite disables ordinary caches under testing; cache probes explicitly enable/bypass relevant layers to reach the reported conditions.

## Second-pass amendments and evidence boundaries

- Added AC-20: real App read-operation dispatch returns a cached Entry title/reference after exclusion while a fresh invocation denies. Only handler resolution is substituted; actor policy and reference validation are real. The reproduced disclosure uses the warm in-process cache. An exploratory retry after clearing that cache reached the handler and denied in the JsonDB fixture; persistent read replay and PostgreSQL command replay therefore remain unqualified, not reproduced findings.
- Added AC-21: accepting an editor invitation for an existing viewer records acceptance but leaves the viewer edge unchanged. The service behavior is reproduced; the endpoint's role projection is source-reviewed.
- Added a direct-viewer-versus-inherited-editor probe for AC-17. Strengthened AC-01 with an authenticated `/auth/me` status/email assertion and a live workspace-role readback, so its claimed admin grant does not rely only on the invitation response.
- A new replay probe initially lacked Entry-level share authority for the fixture owner. That setup error was corrected with an explicit direct Entry grant; it is not a product finding. The final 23-case run passes.
- Corrected the assessment to recognize existing protected-field guards, executable-App trust gates and durable command receipt/outbox machinery. The remaining integration and replay checks are specified separately.
- Reclassified import-broken, externally untraced AC-19 as P2 legacy disposition work; do not resurrect an inactive module to repair it.
- Added 12 requirements (U65–U76) and clarified permission composition, token acceptance versus email verification, anonymous access, durable/dependent grants, operation scope, release-point revocation, external outcomes, migration/restore and measurable acceptance evidence.

Additional existing regression selection, separate from the earlier 290:

```text
test_adr012_queryability_contract.py
test_operation_receipt_errors.py
contract/test_app_operations_trust.py
contract/test_app_operations_idempotency.py
```

These 13 tests exercise existing guards, trust and receipt/idempotency boundary behavior. They do not execute PostgreSQL transactional race qualification. `results.json` records the final probe inventory, source hashes and tested working-tree limitations. The Python runner still reproduces only the characterization selection; existing regression selections are listed separately.

## Reproduce

From the repository root after the documented `uv sync --frozen --extra dev --extra test` setup:

```bash
backend/.venv/bin/python docs/ops/qualification/access-control-review/run_characterization.py
```

The runner disables live-server selection, selects JsonDB test mode, supplies an ephemeral test key, copies the probes to a unique temporary test module and removes that module afterwards. It changes no runtime implementation. After fixes, affected characterization assertions should change/fail: convert those cases to permanent tests of the intended secure behavior, retaining authorized-path coverage.

## Existing-test selections

The initial selection passed all 134 collected cases:

```text
test_permissions.py
test_wave1_access.py
test_visibility_access_cascade.py
test_sharing.py
test_access_api.py
test_invitations.py
test_invitations_security.py
test_shares_api.py
test_workspace_staff_inventory.py
test_workspace_member_update_integrity.py
test_workspace_departure.py
test_ownership_transfer.py
test_permissions_process_cache.py
test_workspace_scope_enforcement.py
test_resource_invitation_revoke.py
test_app_owner_grant.py
```

The second selection passed 132 cases:

```text
test_policy_engine.py
test_policy_engine_requires_approval.py
test_connector_subject_policy.py
test_skill_access_scope.py
test_blank_app_accessible_cache.py
test_agent_tool_scope.py
test_request_scope_guard.py
test_event_subscription_registry.py
test_events_ws.py
test_wave0_tool_context_acl.py
test_tool_context_find_entries_scope.py
test_comment_permission_matrix.py
test_workspace_members_user_leak.py
contracts/test_http_execution_scope.py
contracts/test_resident_tool_execution_scope.py
contracts/test_app_declaration_execution_scope.py
contract/test_app_operations_policy.py
```

The third selection passed 24 cases:

```text
test_audit_log_query.py
test_audit_log_query_gaps.py
test_events_polling.py
test_events_polling_gaps.py
```

All listed paths are relative to `backend/tests/`. Use `backend/.venv/bin/python -m pytest` with an ephemeral `INTEGRAL_CREDENTIAL_ENC_KEY`; fixtures set the remaining test mode. The initial command used repository default pytest addopts and `-q --tb=short`; the later selections used `-o addopts='' -q --tb=short` so counts were explicit. Initial case count was independently confirmed through collection. Successful existing tests can encode the older intended policy, and several event tests mock the real authorization engine, explaining why a green selection coexists with these findings.

## Qualification still required

Full `make verify`, CI-faithful smoke/xdist, clean wheel/Core-only release checks, PostgreSQL multiprocess races, shipped-browser/MCP acceptance, real session/MFA/provider behavior and operational offboarding/recovery are required implementation closure gates. They were not run as part of this source/design review. The source-confirmed policy-subject authority issue and generated-document helper need end-to-end reachability qualification; the synthetic policy probe does not establish a deployed exploit.

Source line references in the review belong to the baseline and may move during implementation. Use function names and retained probe names to locate them afterward. The evidence JSON contains only test identifiers/counts, source baseline/hashes and limitations; it contains no raw tokens, credentials, production records, prompts or completions.
