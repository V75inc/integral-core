# WP-09.2 evidence — fresh-invocation recovery and reconciliation

## Result

The native provider now restores only a complete checkpoint whose encrypted
Core manifest matches the session's safe pointer. A present but invalid pointer
is a hard stop; recovery does not scan past it for a newer snapshot. If a
process stopped before any model request, tool effect, or approval was created,
Core can reconstruct the interrupted turn from its persisted user message.

The chat route stores the `ChatMessage` ID in the corresponding Core
`AgentRun.metadata`. Recovery reads that record only after checking the prior
run's principal, workspace, thread, and native-provider binding. It accepts
exactly one non-empty text part with no provider metadata. Images, attachments,
and enriched turns require reconciliation rather than an incomplete replay.

Model dispatch observations, unresolved or completed tool-effect records, and
pending WorkApprovals all prevent rebuilding the prompt. Unsettled model
requests keep their specific conflict response; settled model activity without
a safe checkpoint still requires reconciliation because the prior response may
have been lost. No model request or tool call is issued by recovery.

## Verification

Command:

```sh
cd backend
INTEGRAL_TEST_DB=postgres \
JVSPATIAL_POSTGRES_DSN=postgresql://integral:integral@127.0.0.1:5433/postgres \
.venv/bin/python -m pytest tests/native_harness/wp_09 \
  tests/native_harness/wp_06/test_provider_stream.py \
  -o addopts='' --strict-markers -q
```

Result: **18 passed** against the configured PostgreSQL test service. Coverage
includes unknown provider outcomes, unresolved tool effects, manifest/pointer
validation, safe text-turn reconstruction, completed-effect blocking, and
scoped text-only message validation.

The prior WP-09.1 evidence remains valid: its focused PostgreSQL suite passed
34 tests, and the complete frontend suite passed 1,304 tests across 220 files.
The previous full `make verify` completed all gates except three listed,
non-harness backend failures recorded in `ledger.yaml`. This slice's broader
repository gate is being rerun after the new recovery changes.

## Remaining WP-09 work

Cross-process approval continuation, kill-at-boundary and two-store divergence
qualification, corrupt codec and key-rotation proof remain open. Browser-visible
provider selection and Prompt Sheet qualification, usage callback reconciliation,
authorized export/retention endpoint behavior, and paid live-model route
qualification are separate open gates. The native provider remains opt-in and
non-default.
