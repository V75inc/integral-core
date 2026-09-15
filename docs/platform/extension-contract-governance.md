# Extension contract governance

**Status:** F0 baseline (publisher/signing remain F3)  
**Companion:** [extension-contract-v1.md](extension-contract-v1.md)

## Contract-test kit

| Lane | Command | Proves |
| --- | --- | --- |
| Core-only | `make verify-core-only` | Import guard + library contains only `core_package` |
| Extension contract | `make verify-contract` | External `examples/reference-hello-app/` load/compile/hooks + install→upgrade→pause→uninstall |

Fixtures: `examples/reference-hello-app/`, `backend/tests/contract/`, `backend/tests/core_only/`.

## Compatibility / support window

- Documented extension surfaces in [extension-contract-v1.md](extension-contract-v1.md) are the support boundary.
- Core minor releases must not break contract tests without a MAJOR bump of the published contract.
- Undocumented Core internals (`app.services.*` outside ToolContext, underscore modules) are unsupported for App authors.

## Semver rules

- **MAJOR** — remove or change meaning of a published ToolContext method, hook point, or required manifest field.
- **MINOR** — additive manifest fields, new optional ToolContext methods, new package classes.
- **PATCH** — clarifications, bug fixes that preserve behavior.

## Deprecation window

Public extension surfaces: announce in CHANGELOG + docs; keep deprecated behavior for at least one minor release of Integral Core before removal.

## Security advisory process

Publisher key rotation, package revocation, and vulnerability disclosure for signed packages are defined before F3. Until then, first-party `trust_tier=trusted` packages are reviewed in-repo.

## Publisher onboarding

Outline only (F3): identity verification, capability review, signing keys, catalog listing metadata. Community Apps default to declarative-only.
