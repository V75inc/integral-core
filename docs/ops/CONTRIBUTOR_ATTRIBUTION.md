# Contributor attribution cleanup

## Scope and recovered decision

The Codex chat **Plan agent contributor removal**
(`01a14757-3c49-74a1-850a-cc06bf650b93`) records the user's instruction to remove
`cursoragent` and `claude` from GitHub Contributors and explicitly leave
`AGENTS.md` unchanged. The target is account attribution in commit messages.
Human authors/committers/coauthors, provider integrations, licenses and product
references retain their existing meaning.

GitHub recognizes [coauthor trailers](https://docs.github.com/en/pull-requests/how-tos/commit-changes/creating-a-commit-with-multiple-authors).
A normal content PR cannot modify messages of existing ancestor commits. Merging
rewritten history back into old history would retain the old attributed commits.

## What PR #145 supplies

- `.githooks/commit-msg`: reject new trailers for the two exact account emails,
  `cursoragent@cursor.com` and `noreply@anthropic.com`, case-insensitively.
- `scripts/commit_attribution.py message <file>`: read-only message check; human
  trailers and mentions of product names are accepted. No automatic stripping or
  reassignment of credit.
- `scripts/commit_attribution.py audit <ref> ...`: read-only JSON report with
  direct matches and exact descendant impact, preserving merge topology in the
  count. Audit mode reports existing attribution without failing the build.
- Regression coverage runs in the pre-commit framework and CI. Local message
  enforcement requires `git config core.hooksPath .githooks`; it does not enforce
  GitHub web messages or hooks in other clones.

These additions prepare the cleanup and discourage new attribution. They do not
remove the accounts from GitHub's historical Contributors display.

## Verified inventory, 2026-10-17

| Snapshot | Commits | Attributed messages | Matching trailers | IDs affected by targeted message rewrite |
|---|---:|---:|---:|---:|
| `main` at `d1725afe` | 1,050 | 45 | 54 | 1,029 |
| PR #145 at `9a0192ed` | 1,060 | 46 | 55 | 1,039 |

The additional attributed commit is the original PR #144 head `4a6850ba`.
The first affected main commit is `989bb1921de83985e38f2cc5529afa354c79efaf`.
Counts are snapshot evidence; rerun the audit after further commits or merges.

The older chat prepared a main-only rewrite at local head `f5e310ef` in
`/private/tmp/integral-core-attribution-main-rewrite`. Its tip tree matches
`d1725afe` (`d867e97e9e57b553345fca98a936bdc2ae6cd0b5`). A fresh read-only audit found 1,050 commits and zero matching trailers there.
This is an old rehearsal,
not the cutover candidate for PR #145's eventual merged contents.

Live main protection currently disables force pushes and enforces protections
for admins. No protection settings or published history were changed by this PR.

## Recommended cutover after PR #145 merges

1. Obtain the explicit ref scope: default-branch history only, or named additional
   branches/tags. Main-only is the narrower proposal. Record the exact approved
   refs and whether published release tags remain unchanged. Do not infer
   permission to retag releases or rewrite every branch.
2. Freeze writes for the cutover. Inventory all heads, tags, open PR bases/heads,
   signatures, and active local worktrees. Save a verified recovery bundle/mirror
   and the old ref IDs outside the live remote. Preserve working files and data.
3. Clone the final approved main into a fresh isolated repository; retain an
   untouched comparison mirror. Rerun the audit on the final merged SHA. Do not
   reuse the earlier rehearsal or rewrite a developer's active checkout.
4. Use the maintained [git-filter-repo message callback](https://github.com/newren/git-filter-repo/blob/main/Documentation/git-filter-repo.txt)
   to remove only complete coauthor-trailer lines for those exact emails. Preserve
   every other message byte, all human author/committer metadata, all file trees,
   commit count and merge-parent topology. Disable empty/degenerate pruning and
   automatic replacement of hash references in messages. Record signature loss
   where rewriting invalidates signatures; never fabricate replacement signatures.
5. Validate the complete old-to-new commit map: each mapped tree identical;
   authors, committers and timestamps identical; each message differs only by the
   targeted trailer removal; parent IDs equal the mapped old parents in order;
   count/topology unchanged; zero targeted trailers reachable from the new main.
   Retain the map and validation output for review.
6. Review the exact candidate, old/new SHA, ref inventory, recovery evidence and
   collaborator/restacking plan. Explicitly approve any temporary force-push
   allowance. [Branch protection](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
   is a separate control; a failed force push is not permission to weaken it.
7. Publish only approved refs with a lease tied to each captured old SHA. Do not
   use a blanket mirror/force push. Restore protection immediately and verify its
   complete prior configuration. Confirm remote main's new SHA and file tree.
8. Rebase/restack or deliberately retire old-history branches before future
   merges. This includes remaining open PR chains; merging an old branch can
   reintroduce stripped ancestry. Unchanged historical tags keep old commits
   reachable by design and must not be silently moved.
9. Verify GitHub's actual Contributors display after refresh/recalculation, not
   only the contributor API (the original chat found those views disagreed).
   Do not claim account removal until the visible result is confirmed. Recheck
   final CI/release references and give collaborators exact recovery instructions.

The historical cutover remains pending explicit scope and protection-change
approval. PR #145 can carry the guard and this reviewed plan; an ordinary merge
of the PR alone does not complete the historical removal.

## Qualification for the PR tooling

Three focused unittest cases pass (exact account matching/case variants,
preservation of human credit/product text, and actual hook/audit behavior in a
disposable Git repository). The all-files pre-commit framework passes, including
the new tooling regression hook. No application runtime code changed in this
addition; runtime/browser evidence remains in the earlier PR #145 review reports.
