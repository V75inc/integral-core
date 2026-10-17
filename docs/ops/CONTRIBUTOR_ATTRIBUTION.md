# Contributor attribution policy

Human author, committer and coauthor credit must remain accurate. Do not add coauthor trailers for `cursoragent@cursor.com` or `noreply@anthropic.com`. Product mentions, provider integrations, licenses and AGENTS.md guidance are separate from commit attribution.

## Prevent new attribution

Configure local message checks:

```sh
git config core.hooksPath .githooks
python3 scripts/commit_attribution.py message /path/to/commit-message
python3 scripts/commit_attribution.py audit HEAD
```

The message check rejects exact account emails case-insensitively without editing the message. Audit mode reports reachable matches and descendant impact. The tests run through pre-commit/CI. Local hooks do not enforce GitHub web commit messages or hooks in other clones.

## Published main cleanup

The approved main-only cleanup was published at `1ee8929cc1e1e084279ca99ad73ac474d3ad9e26` after PR #145 merged. It removed 55 complete coauthor lines from 46 messages across 1,062 commits. Every file tree, human author/committer field, timestamp/timezone, remaining message byte and ordered mapped parent was verified against the original history, including in a fresh remote clone. Two unaffected signatures remain; invalidated signatures are preserved with the original signed objects in verified external recovery bundles.

Release tags and every non-main branch retained their existing IDs. All original main protections were restored, and the temporary account-specific PR bypass was removed. The contributor API showed six human contributors, but the visible GitHub sidebar still listed the two agent accounts at the last readback. GitHub display removal remains a separate confirmation; do not infer it from source or API results.

## Existing branches and recovery

Do not merge or pull old-history branches into rewritten main: that can reintroduce attributed ancestors. Preserve active checkouts and runtime data. Start from fresh main, inspect a branch's unique applicable changes, and restack those changes using the retained old-to-new map. PR #137 and its base chain require reconciliation before integration.

Recovery bundles, complete ref/protection snapshots, commit maps, original signed objects and validation reports are retained outside the repository. Before a live rollback, preserve any subsequent new-main changes, take another verified backup and obtain specific rollback authorization. Restore only the intended ref with an exact lease, restore protection immediately, and never mirror-push or move release tags as a shortcut.

Any future history cleanup requires explicit ref scope, verified recovery, full per-commit content/metadata/topology comparison, exact-SHA leases, narrowly approved protection changes and actual GitHub Contributors readback. A normal content merge cannot remove existing ancestor attribution.
