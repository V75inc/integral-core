# Release qualification

Qualification applies to an exact source, artifact and configuration. Read the
package version from `backend/pyproject.toml`; verify publication and deployment
independently. Merged source, a healthy preview and green CI are separate facts.

## Candidate under review

PR #171 bounds resident model working context across restored runs and exposes
the running Core version. The [context lifecycle evidence](qualification/harness-context-lifecycle.md)
records its reproduction, deterministic limits, cloud-model browser checks and
qualification scope. Source and dependency updates require fresh checks; earlier
results do not qualify a later revision automatically.

## Promotion record

Follow [qualification](QUALIFICATION.md) and [releasing](../../RELEASING.md).
Record source/lock identity, artifact digest and install provenance, database
adapter, non-secret provider configuration, test/browser outcomes, limits and
recovery. Keep raw logs and screenshots in CI/review artifacts. Preserve
unresolved acceptance work in [hardening follow-up](../product/HARDENING_FOLLOW_UP.md).

Older release reports and completed implementation plans remain in Git history;
they are not live instructions or current release acceptance.
