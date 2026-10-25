# Contribute to Integral

Read the root `AGENTS.md`, local module instructions, and [invariants](../INVARIANTS.md) before changing the substrate. Preserve unrelated work in a shared checkout. Keep domain semantics in App packages and hosts.

## Local setup and gates

Install the backend from `uv.lock` with both development and test extras. Install the frontend with `npm ci`. Configure private development settings without committing `.env` files.

```bash
git config core.hooksPath .githooks
make verify
make verify-ci
```

The broad gate covers guards, pinned formatting, types, CI-faithful testing, and both suites. PR CI is narrower. TESTING, missing developer `.env`, and xdist can expose boot errors that an ordinary local pytest run hides.

Most guards inspect the staged index; an unstaged pass can be vacuous. The CSP hash guard checks the working tree. Before a commit, stage the intended changes, run the hooks and relevant tests, and fix real failures. Do not bypass a failure to make a commit appear ready.

## Implementation boundaries

App-specific settings controls MUST use the standard App settings dialog.
Review package, SDK and agent-authored UI against the
[settings UI contract](../platform/app-settings-ui.md) and I-APP-SETTINGS-01.

Use jvspatial endpoints, canonical exception subclasses, schema modules, and service-layer writes. Graph participants attach to a rooted parent in the same unit of work. Use typed edge metadata and Walkers for their intended roles. Measure and document permitted efficiency deviations.

Core cannot import domain packages or branch on their identity. App tools use scoped facades. Changing core skills, bindings, tool manifests, or example Apps requires `make capability-map` and the regenerated artifacts.

## Review evidence

State the problem, changed behavior, relevant tests, and material limitations. Keep source tests, CI, wheel checks, database contracts, browser acceptance, deployment, and publication separate. Verify writes through readback and receipts where appropriate.

Committing requires a clean applicable gate. Pushing and opening/updating PRs require explicit user authorization under the repository's agent discipline. Successful local checks alone do not authorize publication or merging.

## Documentation

Update the current reader guide and affected technical contract together. Avoid adding another sprint report to the public documentation journey. Keep generated artifacts generated and machine fixtures clearly identified. Use the [editorial audit](../editorial/README.md) when consolidating old paths.

## Contributor attribution

Keep human authorship and human coauthor credit. Omit Cursor and Claude account
coauthor trailers from new commit/merge messages. The repository `commit-msg`
hook checks their account email identities when local hooks are enabled. The
**Contributor attribution** workflow also checks raw authors/committers, all
reachable ancestors, and PR title/body trailers. Run
`python3 scripts/commit_attribution.py check HEAD` before publication; use a
complete clone. Review the final merge message as well. These checks do not
remove provider support or change existing history. See the
[history cutover plan](../ops/CONTRIBUTOR_ATTRIBUTION.md) for the separate GitHub
Contributors cleanup and verification requirements.

## Checkout hygiene

Use the primary checkout for new work. Obtain explicit permission before creating
another worktree. Keep only active task branches locally; remove merged task
branches after verifying their remote tips and preserving any uncommitted work.
Do not retire another contributor's active branch or change a development database
as part of source cleanup. Runtime secrets, recovery snapshots and raw QA evidence
belong outside tracked documentation. Maintain the existing topic guides and keep
candidate evidence in the release record or PR/CI artifacts.
