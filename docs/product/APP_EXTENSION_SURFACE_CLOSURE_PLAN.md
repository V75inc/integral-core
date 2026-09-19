# App extension surface closure plan

**Status:** proposed closure plan
**Audience:** Integral Core maintainers and commercial App retrofit teams
**Decision:** an App is a separately versioned artifact that extends Integral
only through the public extension contract. It must never require a Core source
change, an undocumented Core import, or a commercial frontend manifest to run.

## Outcome

Finish an extension surface that lets a public developer, partner, or commercial
team build an App with its own data model, operations, skills, agent personas,
views, scheduled work, and migrations; package it independently; install it in
any compatible Integral Core runtime; and upgrade or recover it without
violating workspace isolation, authorization, or customer customizations.

The first proof is Asset Register. The second proof is one existing commercial
App retrofit performed from its own repository against a version-pinned Core.
Hello remains the fast compatibility fixture, not the product proof.

## What is in place

| Surface | Current evidence | Standard status |
| --- | --- | --- |
| Package discovery and Core-only boot | `INTEGRAL_PACKAGE_PATHS`, package classes, Core-only guards | Implemented; needs installed-artifact proof |
| Declarative data model | App/Track/Entry manifests, relations, settings, seeds, migrations | Implemented; needs external authoring and upgrade proof |
| Operations | Manifest operations, policy and idempotency dispatcher, UI/HTTP/resident/MCP routes | Implemented; public SDK is incomplete |
| Trusted imperative behavior | Signed bundle tools and frozen hooks through `ToolContext` | Implemented for trusted code; capability model and publisher flow incomplete |
| Agent surface | Namespaced declarative JV skills, App agents, schedules, shared runtime context | Implemented for declarative skills; executable custom skills are not runnable by the overlay |
| Views | Core palette views plus static extension-view iframe host and operation bridge | Implemented for static assets; public dynamic/module contribution is deferred and commercial manifests remain a parallel route |
| Lifecycle | Install, settings pause, upgrade, pause/resume, uninstall, scheduler reconciliation, restore tests | Implemented in source; interruption/recovery needs built-artifact and Postgres evidence |
| Developer experience | Scaffold, authoring guide, contract tests, minimal SDK | Present; it does not yet make an external author successful without Core knowledge |
| Release governance | Contract version, semver rules, trust tiers, Core pin runbook | Defined; artifact publication, compatibility certification, revocation, and support evidence need completion |

## Gaps to close before commercial retrofit

1. **One public API, not a runtime-only API.** `integral_sdk.OperationContext`
   currently describes only reads while real Apps require scoped track lookup,
   canonical create/update, conditional mutation, settings, audit, and
   correlation/idempotency behavior. The published protocol, type stubs,
   documentation, error taxonomy, and runtime facade must agree.
2. **One view surface.** A public App can ship static iframe assets, but cannot
   register a dynamic frontend module. Commercial-only `productManifests` are
   therefore a bypass around the public extension contract. The public view
   host needs a supported contribution model, a capability-limited bridge, and
   accessibility, CSP, asset-integrity, and lifecycle guarantees.
3. **Executable agent skills need an honest contract.** Declarative skills are
   viable today. `kind: custom` is catalogued but not executed by the overlay.
   Either ship an explicit, capability-gated executor or remove it from the
   supported manifest until it exists. Do not ask retrofit teams to use a
   metadata-only feature.
4. **Artifact reality must replace source-fixture confidence.** The contract
   lane passes from this checkout. It must additionally build Core and the App
   separately, install both into a clean environment, assert no repo paths or
   private imports are reachable, and retain the exact package/Core digests.
5. **Recovery must be a release gate.** The current local contract lane skips
   Postgres concurrency and restore checks. Upgrade, partial install, failed
   seed, failed migration, restart, and rollback/restore must run against the
   release artifact and a real database before a compatibility claim.
6. **Security and operations need productized ownership.** Trusted-code
   signing works only after operator key configuration. The platform needs an
   explicit publisher identity/key lifecycle, capability declarations,
   revocation behavior, upgrade compatibility policy, audit events, and
   developer-facing diagnostics.
7. **Retrofits need a repeatable extraction path.** Existing commercial Apps
   must be assessed as bundles, with each private Core import, bespoke API,
   frontend manifest, and direct graph mutation either replaced by a public
   capability or registered as a Core-contract gap.

## Locked extension standard v1.1

An App is compatible only if it meets all of the following.

1. **Identity and packaging:** a manifest declares slug, semantic version,
   contract/Core compatibility range, publisher, license, requested
   capabilities, artifact fingerprint, and trust tier. Every executable and
   view asset is covered by the fingerprint.
2. **Data:** it declares App/Track/Entry schema, relations, settings, seeds,
   views, and migrations. Data remains rooted at Workspace → App → Track →
   Entry; cross-App references are explicit and stay inside a workspace.
3. **Business operations:** every app-owned action has a typed operation with
   input/output schema, read/mutation class, policy action, staging level,
   idempotency declaration, timeout, audit behavior, and stable error codes.
   UI, HTTP, resident agents, and external MCP call the same dispatcher.
4. **Agent capabilities:** declarative skills are namespaced and declare their
   allowed tools. Executable skills, if enabled, run only through the same
   capability-limited operation/ToolContext boundary; they do not receive Core
   internals or unscoped graph access.
5. **Views:** an App uses generic Core views or a package-owned extension view
   that receives only a short-lived bridge token and can read scoped context,
   call declared operations, and render approved theming. It cannot reach
   bearer credentials, arbitrary APIs, or host internals.
6. **Lifecycle:** install, setting collection, activation, pause, resume,
   upgrade, uninstall, and recovery are idempotent and observable. Pause
   removes executable registrations; uninstall follows declared retention and
   dependency policy.
7. **Distribution:** the App builds in a separate source tree, uses only
   `integral_sdk` and documented contracts, and installs through
   `INTEGRAL_PACKAGE_PATHS` into a version-pinned Core.

## Delivery plan

### Wave 0 — freeze the contract and establish evidence

**WP-00: Extension compatibility ledger**

- Publish a machine-readable `extension-contract.json` alongside the prose
  contract. It lists manifest version, SDK version, hook catalog, bridge
  methods, operation semantics, trust requirements, and deprecations.
- Generate SDK reference documentation from protocol/type definitions and add
  a contract-diff check that fails undocumented public changes.
- Create a commercial-App intake matrix that inventories each App's data,
  operations, skills, views, jobs, integrations, private imports, and current
  Core pin.

**Acceptance evidence:** a prospective App author can determine support solely
from the published contract; every commercial dependency is classified as
supported, adapter-needed, or a Core gap.

### Wave 1 — make the public capability boundary complete

**WP-10: Complete and version the SDK**

- Expand `OperationContext` into typed, documented capability groups:
  scoped reads, track/type resolution, canonical writes, conditional updates,
  settings, audit/change events, and operation metadata.
- Define return models and stable error codes; use them in the dispatcher,
  HTTP API, MCP schema, and bridge.
- Add negative tests proving that a bundle cannot import `app.models`,
  `app.services`, private SDK names, or use an undeclared capability.

**WP-11: Normalize App operations**

- Require each operation to declare policy, staging, idempotency, timeout,
  audit event, and result schema.
- Add generated client schemas for UI, resident agent, and MCP; test equivalent
  allowed, denied, invalid, duplicate, and replay cases through all four.
- Remove domain-shaped compatibility helpers from the public facade. Where a
  capability is truly generic, replace it with a generic relation/query
  primitive rather than another named domain method.

**Acceptance evidence:** Asset Register compiles with strict SDK typing and
uses no undocumented Core imports; each operation has the same observable
outcome across UI, HTTP, resident, and MCP.

### Wave 2 — finish the app-owned experience surface

**WP-20: Promote the extension view host to the public route**

- Decide and implement one supported model for package-owned frontend code:
  static module bundle in the existing sandboxed host, with an explicit module
  manifest, or a restricted component contribution API. Retire the
  commercial-only `productManifests` path after migration.
- Version the bridge and enforce origin, token expiry, CSP, asset digest,
  message schema, focus handling, keyboard navigation, and error boundaries.
- Expose only contextual read and declared operation invocation; support
  offline/loading/error states and host-led theme changes.

**WP-21: Resolve custom skill execution**

- Choose one of two truthful v1.1 outcomes: implement a sandboxed executor for
  trusted custom skills, or reject `kind: custom` during compilation and mark
  it v2-only. The recommended path for commercial Apps is the executor, with
  publisher trust, declared capabilities, cancellation/time limits, structured
  logs, and workspace-scoped context.
- Keep community Apps declarative-only until an isolated, capability-restricted
  execution runtime exists.

**Acceptance evidence:** Asset Register's custom detail surface and one
custom-skill App work from independent artifacts. A hostile iframe message,
expired bridge token, modified asset, and undeclared skill capability are all
rejected.

### Wave 3 — prove lifecycle, artifacts, and multitenancy

**WP-30: Independent artifact contract lab**

- Move Asset Register to its own repository/package build. Build a Core image
  and App artifact independently, then install into a clean Postgres-backed
  environment without the Core checkout or commercial source tree mounted.
- Record Core SHA/tag, App SHA/version, contract version, platform image
  digests, package fingerprint, environment, and full test results as the
  release evidence.

**WP-31: Lifecycle and recovery certification**

- Run install, settings pause/resume, upgrade with retained customer fields,
  pause/resume, uninstall/dependency blocking, scheduler deduplication,
  interrupted install/seed/migration, restart, restore, and unsupported
  downgrade tests against real Postgres.
- Add two-workspace/two-user concurrent tests for operation, skill, agent,
  view bridge, schedule, and cache isolation.
- Fix warnings and make time handling timezone-aware in the reference App.

**Acceptance evidence:** all Core-only, contract, artifact, browser, and
Postgres recovery lanes pass with zero skipped required checks; an evidence
bundle is attached to the release candidate.

### Wave 4 — retrofit the commercial portfolio

**WP-40: Retrofit pilot**

- Pick one commercial App with a real view, business operation, skill, and
  scheduled or background behavior. Extract it into `packages/apps/<slug>` in
  the commercial repository, pin it to the certified Core release, and forbid
  edits to vendored Core files.
- Use adapters only inside the App for its own legacy data/API transition. A
  missing generic primitive becomes a Core work item with a contract test,
  never a private import exception.
- Perform side-by-side acceptance against the existing commercial behavior,
  migrate a representative workspace copy, and document the rollback/export
  route.

**WP-41: Retrofit factory**

- Turn the pilot into a template: extraction checklist, manifest/operation
  mapping, skill conversion guide, view migration guide, Core-gap request
  template, and CI pipeline that consumes the Core pin.
- Establish compatibility certification: each App declares the Core range it
  has passed against, and a Core release runs certified App contract suites
  before promotion.

**Acceptance evidence:** the pilot ships without a Core fork or private Core
import; a second App can be assessed and scaffolded using the factory without
inventing a new extension mechanism.

## Dependency order and ownership

| Order | Work packages | Owner | May proceed when |
| --- | --- | --- | --- |
| 0 | WP-00 | Core architecture | The compatibility ledger and commercial intake matrix are approved |
| 1 | WP-10, WP-11 | Core runtime/API | Typed SDK and shared operation semantics are contract-tested |
| 2 | WP-20, WP-21 | Core experience/agent runtime | View and custom-skill decisions are implemented, not merely documented |
| 3 | WP-30, WP-31 | Core release engineering | Independent artifact and Postgres evidence are green |
| 4 | WP-40 | Commercial App team with Core liaison | Certified Core tag and public surface are available |
| 5 | WP-41 | Platform enablement | Pilot acceptance and Core-gap disposition are complete |

Every work package returns: changed contract/version, tests and exact
environment, artifact identifiers, migration behavior, security implications,
known limitations, and any new Core-gap request. Core owns a gap until it is
documented, versioned, and tested; commercial Apps never patch around it.

## Release criteria

Do not call the surface ready for commercial retrofits until all are true:

- An external Asset Register artifact passes strict SDK type checking and a
  clean installation against a tagged Core artifact.
- `make verify-core-only`, `make verify-contract`, artifact installation,
  browser view-host tests, and the required Postgres concurrency/recovery lane
  pass for the exact release candidate without skipped required checks.
- A real custom App view uses the public host rather than a commercial frontend
  manifest.
- The custom-skill manifest state matches runtime reality: runnable with
  enforced trust/capabilities, or rejected as unsupported.
- All four execution paths call the same typed operation contract and produce
  equivalent authorization, validation, idempotency, audit, and error results.
- A commercial pilot is pinned to Core and contains no Core-source edits or
  undocumented imports.
- The release evidence includes Core/App hashes, compatibility ranges,
  artifact signatures, support limitations, and a rollback/restore rehearsal.

## Immediate next actions

1. Lock the v1.1 boundary in WP-00, beginning with the SDK capability gap and
   the decision on custom-skill execution.
2. Create the independent Asset Register build pipeline and make its clean
   Core installation the gating test for every remaining surface change.
3. Replace or migrate the commercial-only frontend manifest route through the
   public extension view host before selecting the first commercial retrofit.
4. Choose the pilot commercial App using the intake matrix, then turn every
   missing primitive found during extraction into a versioned Core contract
   task.
