# Contributor attribution policy

Human author, committer and coauthor credit must remain accurate. Do not attribute commits to `cursoragent@cursor.com`, `noreply@anthropic.com`, or the `cursoragent` / `claude` GitHub noreply identities. Product mentions, provider integrations, licenses and AGENTS.md guidance are separate from commit attribution.

## Prevent new attribution

Configure local message checks:

```sh
git config core.hooksPath .githooks
python3 scripts/commit_attribution.py message /path/to/commit-message
python3 scripts/commit_attribution.py audit HEAD
python3 scripts/commit_attribution.py check HEAD
```

The local message check rejects coauthor trailers for the targeted account emails
case-insensitively without editing the message. `audit` reports reachable trailer
matches and descendant impact; it is an inventory command and does not fail on
matches. `check` fails on targeted coauthors or raw author/committer email
identities anywhere in the selected refs' reachable history. It requires complete
history, so a shallow clone must fetch its ancestors first.

The separate **Contributor attribution** workflow runs for every PR, including
documentation-only changes and title/body edits, for main pushes, and for merge
groups. It checks GitHub's synthetic PR merge commit and all ancestors, so merging
an old attributed branch cannot hide behind a clean tip. It also checks PR title
and body trailers that could become squash text. Event text is read as JSON data,
never interpolated into shell commands. Human coauthors and ordinary provider
mentions remain valid. The check does not rewrite history or remove content.

After the workflow lands, configure **Contributor attribution** as a required
status check in the protected branch/ruleset. Adding the workflow alone does not
change repository protection. Local hooks still depend on each clone's setup.
Review the final GitHub merge/squash message: text edited in the merge dialog
after checks run cannot be inspected by a PR check. The main push check detects
such attribution after the merge; it cannot prevent that merge retrospectively.

## Published main cleanup

The approved main-only cleanup was published at `1ee8929cc1e1e084279ca99ad73ac474d3ad9e26` after PR #145 merged. It removed 55 complete coauthor lines from 46 messages across 1,062 commits. Every file tree, human author/committer field, timestamp/timezone, remaining message byte and ordered mapped parent was verified against the original history, including in a fresh remote clone. Two unaffected signatures remain; invalidated signatures are preserved with the original signed objects in verified external recovery bundles.

Release tags and every non-main branch retained their existing IDs. All original main protections were restored, and the temporary account-specific PR bypass was removed. The contributor API showed six human contributors, but the visible GitHub sidebar still listed the two agent accounts at the last readback. GitHub display removal remains a separate confirmation; do not infer it from source or API results.

## Existing branches and recovery

Do not merge or pull old-history branches into rewritten main: that can reintroduce attributed ancestors. Preserve active checkouts and runtime data. Start from fresh main, inspect a branch's unique applicable changes, and restack those changes using the retained old-to-new map. Audit each proposed branch before integration; the historical main cleanup did not clean other branches or release tags.

## GitHub contributor display

Read back the public repository sidebar as well as the Contributors view. The
REST contributors endpoint can omit coauthors, so an API list without the target
accounts is insufficient proof of display removal. A normal PR can prevent new
attribution; it cannot remove old ancestor messages or guarantee a cached display
refresh.

[GitHub's contributor guidance](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-a-projects-contributors#contributor-data-is-stale-after-history-changes)
allows about 24 hours for displays/statistics to refresh after history changes,
then directs owners to Support. Include the clean main SHA, metadata/trailer
audit, sidebar accounts, API discrepancy, and remaining branch/tag scope in a
refresh request. Ask GitHub to confirm the cause rather than assuming that old
tags or a particular cache are responsible. This is a display correction, not a
sensitive-data purge: GitHub's purge policy excludes non-sensitive data, and
original objects can remain in PR refs, forks and clones.

Recovery bundles, complete ref/protection snapshots, commit maps, original signed objects and validation reports are retained outside the repository. Before a live rollback, preserve any subsequent new-main changes, take another verified backup and obtain specific rollback authorization. Restore only the intended ref with an exact lease, restore protection immediately, and never mirror-push or move release tags as a shortcut.

Any future history cleanup requires explicit ref scope, verified recovery, full per-commit content/metadata/topology comparison, exact-SHA leases, narrowly approved protection changes and actual GitHub Contributors readback. A normal content merge cannot remove existing ancestor attribution.
