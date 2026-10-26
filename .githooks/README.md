# Repository hooks

Enable the checked-in hooks from the repository root:

```bash
git config core.hooksPath .githooks
```

The pre-commit hook checks jvspatial conventions, graph attachment, substrate domain drift, private App imports, service-layer writes, UI conventions, skills, bundle facades, tool manifests, CSP script hashes, invalid destroy calls, and count-by-hydration drift.

Most guards scan the staged index. Stage the intended changes before treating their result as evidence. The CSP guard checks working files because a stale header breaks the browser even when the build passes.

Run the relevant tests and clean formatting/types before committing. Bypassing a genuine failure is forbidden; any exceptional bypass must be explicitly justified under repository discipline. Use `make verify` for the broad local gate and `make verify-ci` for CI-faithful smoke testing.

See the [contributor guide](../docs/developer/CONTRIBUTING.md) and [invariants](../docs/INVARIANTS.md).

## Commit attribution

The `commit-msg` hook rejects new `Co-authored-by` trailers for the Cursor and
Claude account email identities. It leaves human coauthors and ordinary product
references intact and does not edit messages. It applies to local commits when
`core.hooksPath` is enabled; GitHub web commits and other clones can bypass local
hooks. The **Contributor attribution** workflow checks complete reachable commit
history, raw author/committer identities, and proposed PR title/body trailers.
It runs independently of application CI path filters. Configure it as a required
status check after it lands; a workflow alone does not change branch protection.

Read-only inventory and rewrite-impact report:

```bash
python3 scripts/commit_attribution.py audit origin/main HEAD
python3 scripts/commit_attribution.py check HEAD
```

Existing attribution requires a separate reviewed history cutover; see the
[cutover plan](../docs/ops/CONTRIBUTOR_ATTRIBUTION.md).
