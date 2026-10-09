# Repository hooks

Enable the checked-in hooks from the repository root:

```bash
git config core.hooksPath .githooks
```

The pre-commit hook checks jvspatial conventions, graph attachment, substrate domain drift, private App imports, service-layer writes, UI conventions, skills, bundle facades, tool manifests, CSP script hashes, invalid destroy calls, and count-by-hydration drift.

Most guards scan the staged index. Stage the intended changes before treating their result as evidence. The CSP guard checks working files because a stale header breaks the browser even when the build passes.

Run the relevant tests and clean formatting/types before committing. Bypassing a genuine failure is forbidden; any exceptional bypass must be explicitly justified under repository discipline. Use `make verify` for the broad local gate and `make verify-ci` for CI-faithful smoke testing.

See the [contributor guide](../docs/developer/CONTRIBUTING.md) and [invariants](../docs/INVARIANTS.md).
