# Release qualification

Qualification applies to an exact source, artifact and configuration. Read the
package version from `backend/pyproject.toml`; verify publication and deployment
independently. Merged source, a healthy preview and green CI are separate facts.

## Candidate evidence

The active candidate is identified by its assembled source revision, package
metadata and built artifact, not by a historical PR number. Keep its exact-head
CI, local aggregate, installed-wheel smoke, migration/recovery and downstream
acceptance results in the release's CI/review evidence. Do not carry a previous
candidate's passing status forward after source or dependency changes.

The [context lifecycle evidence](qualification/harness-context-lifecycle.md)
remains scoped to that feature's tested revision; it is not the current release
record. Apply the [documentation acceptance checklist](../../RELEASING.md#documentation-acceptance-checklist)
before promoting any candidate.

## Promotion record

Follow [qualification](QUALIFICATION.md) and [releasing](../../RELEASING.md).
Record source/lock identity, artifact digest and install provenance, database
adapter, non-secret provider configuration, test/browser outcomes, limits and
recovery. Keep raw logs and screenshots in CI/review artifacts. Preserve
unresolved acceptance work in [hardening follow-up](../product/HARDENING_FOLLOW_UP.md).

Older release reports and completed implementation plans remain in Git history;
they are not live instructions or current release acceptance.
