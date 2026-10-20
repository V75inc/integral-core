# Current qualification records

Qualification belongs to an exact source, artifact and configuration. This index
is not a package-publication or deployment status page. Read the source version
from `backend/pyproject.toml`; verify the release index and deployment separately.

## Work under review

- **rc28 / PR #162 — App ownership, durable collection updates, Track loading and
  semantic retrieval.** [Owner/batch/performance evidence](../qa/2026-10-20-owner-batch-performance.md)
  and [semantic indexing evidence](qualification/semantic-search-2026-10-20/REPORT.md)
  record the local checks and limits. The [rc28 release update](qualification/release-rc28.md) records version consistency, artifact identity and CI findings. Semantic evidence includes the labelled
  corpus, real encoder/pgvector, browser source readback, latency and actual
  provider token observations. It does not qualify a production deployment.
- **rc27 / PR #160 — chat, authoring and usability.** The
  [rc27 record](qualification/release-rc27.md) retains that candidate's exact
  baseline and validation. Its results do not qualify later PR #162 changes.

## Historical evidence

The [rc20 qualification](qualification/release-rc20.md) preserves its original
wheel digest, Business/browser readback and limitations. It is historical evidence
rather than the current release gate. Other retired reports remain in Git history.

## Promote a candidate

Follow [qualification](QUALIFICATION.md) and [releasing](../../RELEASING.md).
Record source and artifact identity, lock/install provenance, actual database
and provider configuration, browser outcomes, limitations and recovery. Use
[hardening follow-up](../product/HARDENING_FOLLOW_UP.md) for open acceptance work.
A green local test, remote CI, publication and deployment remain separate facts.
