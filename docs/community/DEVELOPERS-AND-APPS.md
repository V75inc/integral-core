# Build with Integral Core

Core is the public Apache-2.0 foundation for Integral. Contribute improvements,
extend the neutral engine/UI and build independent Apps, including commercial
Apps. No Integral account, Business subscription or marketplace listing is
required to clone/build the source or develop an App. Preserve actual component
licenses and notices; the official One registration flow does not add restrictions
to Apache source rights.

## Where work belongs

| Project | Contribute here |
|---|---|
| Core | Graph/schema APIs, access invariants, neutral editor/views, resident harness, SDK/ToolContext, package validation, portable data/runtime and neutral hosting contracts |
| Integral One | Desktop host/package, onboarding, branding, native integration, recovery and official account/catalog/migration clients |
| Independent App package | Domain schemas, views, skills, tools and tested operational workflows |
| Integral Business | Private maintained Apps, hosted/commercial composition and services; no private source/customer data copied into Core |

See [CONTRIBUTING](../../CONTRIBUTING.md), [AGENTS](../../AGENTS.md),
[invariants](../INVARIANTS.md), [extension contract](../platform/extension-contract-v1.md)
and [contract governance](../platform/extension-contract-governance.md).

## Start developing

Use the repository's supported Python/Node versions and locked dependencies:

```sh
cd backend
uv sync --frozen --extra dev --extra test
.venv/bin/python -m app.main
# Separate terminal, from the repository root:
cd frontend
npm ci
npm run dev
```

Follow the root README for environment setup. Never copy production credentials
or databases into a development checkout. Core developer/server mode retains
multi-user authorization; One selects its separately enforced single-owner
profile. Model credentials are optional for manual data/App work.

Read the extension contract, then inspect `examples/reference-hello-app` and the
other bounded reference packages. Start with a declarative App using supported
schema/views. Validate its manifest/package using the documented compiler/import
path and run its contract checks before asking for review. Use the ToolContext
facade for allowed tools; do not import backend internals or treat a package's
self-declared trust flag as execution authority. Keep executable/native extension
admission distinct from declarative package support.

## Contributor workflow and review

Open a focused issue or draft change with the intended outcome and evidence.
Maintainers review compatibility, graph/access invariants, tests, readability,
security and user impact. Run `make verify` and the relevant contract/platform
gates for implementation, and report actual results and any unavailable gates.
Use small reviewable changes and explicit deprecation/migration notes for public
contract changes. Preserve Apache contribution terms and third-party provenance.
No copyright assignment or proprietary-relicensing instrument is required merely
to use Core; any future contribution instrument needs separate explicit review.
AI-assisted submissions retain human ownership, verification and accountability.

## Marketplace path (planned)

The marketplace is not launched. File import and reference packages are useful
now. The next stages are publisher enrollment, explicit package licensing and
compatibility metadata, curated signed declarative artifacts and anonymous
catalog browsing with account-based official acquisition/restoration. Paid
commerce follows only after its publisher/payout/refund/tax, license/team access,
entitlement and data-continuity contracts are qualified.

Publishers retain their applicable rights and may choose compatible open-source
or commercial terms. Core/SDK Apache rights do not turn independent Apps into V75
property. Metadata must identify publisher, license, version, Core compatibility,
dependencies, capabilities/network use and artifact digest. Signing establishes
provenance, not a sandbox. Moderation, takedown/appeal, security reporting and
maintenance/version policy must be published before broad submission opens.

## Near-term roadmap and ownership

1. Publish reproducible Core/One builds and contribution/reference App guides.
2. Qualify contract compatibility and Core changes upstream, without a second
   engine/frontend fork in One.
3. Deliver the shared account/activation service while preserving local data.
4. Open curated publisher submissions/catalog, then separately qualified payments.
5. Connect existing accounts to Business via authorized workspace selection and
   explicit backup/consent/data reconciliation; no silent upload or sync promise.

V75 maintainers own triage and final release/contract decisions. Maintainer response
times, marketplace revenue share and production availability are not promised by
this document. Track work publicly in the relevant repository issues/PRs; service
operations and private Business commercial implementation stay in their owner.
