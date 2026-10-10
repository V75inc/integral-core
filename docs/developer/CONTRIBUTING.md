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

Use jvspatial endpoints, canonical exception subclasses, schema modules, and service-layer writes. Graph participants attach to a rooted parent in the same unit of work. Use typed edge metadata and Walkers for their intended roles. Measure and document permitted efficiency deviations.

Core cannot import domain packages or branch on their identity. App tools use scoped facades. Changing core skills, bindings, tool manifests, or example Apps requires `make capability-map` and the regenerated artifacts.

## Review evidence

State the problem, changed behavior, relevant tests, and material limitations. Keep source tests, CI, wheel checks, database contracts, browser acceptance, deployment, and publication separate. Verify writes through readback and receipts where appropriate.

Committing requires a clean applicable gate. Pushing and opening/updating PRs require explicit user authorization under the repository's agent discipline. Successful local checks alone do not authorize publication or merging.

## Documentation

Update the current reader guide and affected technical contract together. Avoid adding another sprint report to the public documentation journey. Keep generated artifacts generated and machine fixtures clearly identified. Use the [editorial audit](../editorial/README.md) when consolidating old paths.
