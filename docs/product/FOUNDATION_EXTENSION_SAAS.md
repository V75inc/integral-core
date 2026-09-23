# Foundation, extension, and SaaS direction

**Status:** Proposed roadmap and architecture direction
**Date:** 2026-09-15
**Companion:** [ROADMAP.md](ROADMAP.md) §2.1

## Purpose

Integral should become an extensible foundation on which any App-based
extension can be built without coupling that extension to Integral internals.
The base must be independently maintainable, testable, releaseable, and
deployable—even when no first-party App, Operational Model, or App-owned view is
installed.

This supports an open-core model:

- **Integral Core** is the open, general-purpose substrate and runtime.
- **Apps** are independently versioned packages that add domain data,
  operations, skills, views, and integrations through published contracts.
- **Commercial Apps** are entitled packages distributed through a commercial
  catalog. Their entitlement is enforced by the platform at installation and
  use, rather than by scattering license checks through core or App code.
- **Stripe Billing** is the first commerce provider, behind an entitlement
  boundary that leaves open-core and self-hosted deployments viable.

This is a product and engineering boundary, not merely a repository split.

## Canonical package vocabulary

These terms are deliberately distinct. Documentation, APIs, catalog UX, and
audit records must use them consistently.

| Term | Meaning |
| --- | --- |
| **Package artifact** | An immutable, versioned, signed distributable containing a manifest and optional approved assets/code. |
| **Package source** | The publisher-controlled repository and build pipeline from which artifacts are produced. It is not installed into a customer workspace. |
| **Catalog listing** | Discoverable metadata for one or more package artifacts: publisher, compatibility, pricing/entitlement, requested capabilities, support status, and release notes. |
| **Installed App instance** | The workspace-local materialization of one package artifact, with its own configuration, lifecycle state, data, grants, and upgrade history. |
| **Operational Model** | The declarative schema and operational component that shapes a Track or App. It may be packaged, but is not synonymous with an App Package or Installed App. |
| **Plugin** | An executable extension capability—backend handler, connector adapter, or frontend renderer—not a synonym for an App. |
| **Publisher** | The accountable identity that signs an artifact and is subject to catalog, security, and revocation policy. |

An installed App instance records the immutable package artifact identity it was
created from. Updating an App creates an auditable lifecycle transition from
one artifact version to another; it never silently follows a mutable catalog
listing.

## The architectural boundary

### Integral Core owns

Core contains the capabilities that every installation and every extension can
rely on:

- Workspace, App, Track, Entry, relation, Operational Model, and attachment
  primitives
- Graph containment, schema validation, migrations, version compatibility, and
  package lifecycle
- Authentication, workspace scope, policy evaluation, collaboration, audit,
  provenance, staging, and approvals
- Generic API, MCP surface, agent-runtime binding, background-task runtime,
  event stream, retrieval, and observability
- Generic rendering primitives and the standard view palette, field widgets,
  action framework, and plugin host
- Extension registry, package verification, compatibility negotiation,
  installation, upgrade, rollback, and uninstall protocols
- Entitlement interface, usage-meter interface, and billing-provider adapter
  interface

Core must not contain domain-specific assumptions such as HR employee fields,
payroll calculations, CRM stages, country filing rules, or a commercial App's
custom view implementation.

### Apps own

Apps own domain intent and only reach Core through published facades and
declarative manifests:

- Entry types, fields, relations, tracks, seed data, taxonomies, and domain
  migrations
- Domain operations, lifecycle-hook bindings, and agent skills
- App-specific view composition and, where necessary, signed App-owned view
  plugins that target the public view-plugin contract
- Connector mapping, sync configuration, domain deduplication, and approved
  outbound actions
- App-level documentation, test fixtures, and capability declarations

Apps must not import Core models or services directly. The existing bundle
facade rule is the correct starting discipline; it must become a complete,
published extension contract.

### Capability, trust, consent, and entitlement

Four separate gates apply to an App request. None implies another.

| Gate | Question answered | Example |
| --- | --- | --- |
| Publisher identity | Who produced this artifact? | A catalog verifies the publisher signature and release provenance. |
| Execution trust | May this code execute in this deployment? | A community declarative package may install, while an executable connector is denied. |
| Tenant consent | Has this workspace administrator approved this requested capability? | An admin approves `connector.read` for a named external account. |
| Commercial entitlement | Is this customer licensed for the package/capability/limit? | The workspace has a licence for Payroll and has not exceeded its connector allowance. |

Capabilities must be machine-readable and policy-addressable. Initial
capability families include `network.egress`, `connector.read`,
`connector.write`, `background.schedule`, `agent.execute`, `view.plugin`,
`attachment.read`, and `attachment.write`. A package declares what it requests;
Core evaluates the request through trust, tenant consent, policy, and
entitlement at the relevant execution boundary.

For the first commercial release, the safe default is:

- Community and third-party commercial Apps are declarative and use Core's
  generic views and registered MCP/tool surfaces.
- Executable backend code is limited to approved first-party or partner
  packages, runs through a least-privilege façade, and has bounded time,
  memory, network, retry, and secret access.
- Dynamic frontend modules are deferred until the platform has a browser
  sandbox, CSP model, CSS isolation, signing/key rotation, revocation, and a
  generic fallback renderer. A valid signature is publisher identity, not a
  sandbox.

Executable package distribution is a software supply-chain concern. It
requires publisher-key rotation/revocation, artifact hashes, SBOMs,
vulnerability review, release provenance, capability review, and audit events
for install and execution.

### The completion test

**Foundation Phase One: COMPLETE (2026-09-15).**

Unlock proofs for tests 1–5 ship on `staging/foundation`. Domain Apps live
under `packages/apps/` (not in the Core tree). Open `integral-core` depends on
this layout; commercial Integral **pins** Core releases (see
[INTEGRAL_CORE_EXTRACT.md](INTEGRAL_CORE_EXTRACT.md),
[CORE_PIN.md](CORE_PIN.md)).

Integral Foundation is complete enough for this boundary when all of the
following are true:

1. A Core-only installation boots, accepts users and workspaces, exposes its
   generic views and APIs, and passes its complete test suite with no domain
   App installed.
2. A separately built sample App can be installed, upgraded, disabled, and
   uninstalled using only documented schemas, facades, and package protocols.
3. Core has no runtime imports of commercial package code, no required seed
   data from commercial packages, and no domain-specific conditionals.
4. An App can include its own declarative views or signed view plugin without
   modifying the core frontend registry.
5. A paid App can be made unavailable through entitlement while the Core and
   unrelated Apps remain healthy and its data remains governed by explicit
   retention/export policy.

## Extension contract

The extension contract is the product API for App authors. It should be
versioned, documented, contract-tested, and intentionally smaller than Core's
internal API.

| Extension concern | Published contract | Non-negotiable rule |
| --- | --- | --- |
| Data | Operational Model/App manifest: tracks, entry types, fields, relations, taxonomies, seed and migration declarations | App data is rooted through the normal Workspace → App → Track → Entry chain. |
| Operations | Tool manifest, typed parameters/results, lifecycle hook declarations, `ToolContext` facade | Operations are idempotent where retried, policy-checked, provenance-stamped, and staged when they cause consequential effects. |
| Skills | Declarative skill format, allowed-tool list, grounding and staging requirements | Skills describe domain procedure; they do not bypass policy or call unregistered code. |
| Views | Generic view configuration plus a signed/versioned view-plugin interface when configuration is insufficient | Generic widgets remain in Core; App visuals ship with the App and communicate only through public contracts. |
| Integrations | Connector manifest, OAuth/secret declaration, field mapping, sync cursor, dedup/conflict policy, outbound-action declaration | External systems are mirrored with provenance; writes back are explicit, auditable, rate-limited, and staged. |
| Lifecycle | Semver compatibility range, install/upgrade/uninstall/recovery hooks, migration and rollback declarations | A failed upgrade must leave the last usable App state intact and observable. |

### Package classes

Define package classes explicitly rather than treating every profile alike:

- **Core package:** ships with Integral and has no domain entitlement.
- **Community App:** declarative-only unless elevated through a reviewed trust
  process.
- **Verified App:** signed package with a permitted connector or view-plugin
  capability.
- **Commercial App:** signed, catalog-distributed package requiring a named
  entitlement.
- **Private organization App:** organization-scoped package with its own
  publisher and access policy.

Package identity, signature, publisher, compatibility range, requested
capabilities, and entitlement key must be inspectable before installation.

### App operational contract

Every App must publish operational metadata alongside its schema:

- Supported read, propose, execute, and destructive operations
- Required policy action, capability, and approval/staging level per operation
- Idempotency key, retry semantics, timeout, and compensation/undo behavior
- Data classification, residency, retention, and export obligations
- App and external-system dependencies, health checks, and degraded behavior
- Audit/provenance events and operational telemetry emitted
- Upgrade, downgrade, uninstall, and entitlement-loss behavior

This metadata makes an App understandable to workspace administrators, the
resident, external agents, and support staff. A skill or tool that cannot state
its operation contract is not a supported extension surface.

### Cross-App concepts without a premature ontology

Apps may declare a small, stable canonical-concept identifier for concepts they
own or consume, such as `organization.legal_entity`, `people.employee`, or
`finance.vendor`. Relation contracts identify the target concept in addition to
the concrete target EntryType. This gives agents and package authors an
interoperability vocabulary without introducing a central ontology engine.

The platform will add an ontology layer only when repeated, evidenced
cross-package mapping requirements justify its complexity.

### Customer data continuity

A commercial entitlement affects use of package behavior; it must never make
customer data opaque or silently disappear. Every commercial App declares:

- Whether it becomes read-only, disabled, or unavailable at the end of a
  trial/grace period
- Which generic Core views remain available for historical records
- Export formats for entries, relations, attachments, and audit/provenance
  metadata
- The treatment of scheduled jobs, connector syncs, outbound credentials, and
  dependent cross-App references
- Data-retention, deletion, and recovery timing after cancellation

Core retains a generic, permission-checked record and attachment exporter so a
customer can retrieve data even when an App-specific renderer is no longer
entitled or its publisher is revoked. Package-specific data must remain
interpretable as typed Core records and manifest metadata.

## Foundation roadmap: bottom-up

### F0 — Core separation and compatibility baseline

**Status (2026-09-15):** Phase One + closeout in progress in-monorepo.
Logical Core/App split, Core Docker `--target core`, contract kit, and
lifecycle E2E for `reference-hello-app` land before F1. Physical multi-repo
extract (`integral-core`) waits until completion tests 1–3 stay green.

**Outcome:** Core can ship without domain Apps and extensions have a stable
surface.

- Define the Core/App repository and distribution boundary.
- Extract first-party and commercial profiles from the Core release artifact.
- Publish manifest, ToolContext, view-plugin, connector, and lifecycle
  contracts with semver rules.
- Add Core-only, external-sample-App, and no-internal-import CI lanes.
- Publish extension-contract governance: compatibility/support window,
  deprecation policy, security-advisory process, contract-test kit, fixtures,
  reference implementations, and publisher review/onboarding procedure.
- Prove the lifecycle with a deliberately tiny external reference App before
  using Organization, HRM, or Payroll as the contract test.
- Make App install, disable, upgrade, rollback, and uninstall recoverable
  transactions with clear diagnostics.

**Exit test:** An independently built sample App is installed into a Core-only
deployment and passes end-to-end contract tests without a Core code change.

### F1 — Trustworthy substrate operations

**Status (2026-09-15):** **Phase One (Admin Forensic Loop) shipped** on
`staging/foundation`. Remaining F1 bullets (retrieval re-rank /
citations, migration recovery UX, broad OTEL dashboards) are later waves.

**Phase One ships:** `POST /api/policies/explain` (dry-run), App
`operations[]` on admin detail, Settings → Audit log over `GET /api/audit-log`
with deny/failure filters, ProvenanceBadge / ActivityPanel deep-links into
Audit log. Golden path: allow + deny + success write without DB access.

**Outcome:** Every extension runs under one explainable, observable control
plane.

- Finish the readable policy grant chain and mature policy administration.
  *(Phase One: explain API + policy_chain in product.)*
- Make provenance first-class and visible across native, agent, computed, and
  connector-originated records.
  *(Phase One: ProvenanceBadge ↔ Audit log correlation.)*
- Deliver append-only, durable workspace events with polling only as fallback.
  *(Phase One: Audit Log UI consumer; WS fanout remains later.)*
- Finish retrieval correctness: freshness, deletion/re-embedding behavior,
  re-ranking, citations, and performance budgets. *(Later wave.)*
- Complete schema migration safety: impact preview, progress, recovery, and
  large-workspace execution model. *(Later wave.)*
- Add production observability for policy denials, events, syncs, agent turns,
  background jobs, and migrations. *(Later wave; deny events already durable.)*

**Exit test:** An administrator can explain an App action's authority and
provenance, trace it through audit events, and diagnose a failed operation
without database access.

### F2 — Extension runtime and operating services

**Status (2026-09-15):** **Phase One (App-owned declarative views) shipped** —
unlocks foundation completion test 4's declarative half. Signed FE
view-plugin hot-load, connector SDK, and full F2 exit test
(mirror → propose → execute) remain later waves.

**Phase One ships:** `reference-hello-app` declares ``view_types[]`` composite
``hello_board`` (base ``composable_board``) + track ``views[]``; contract
tests prove compile/materialization with **no** new Core
``frontend/src/views/manifests/*.manifest.ts``. FE still renders via Core
palette composites (`ViewRenderer` composite-aware).

**Outcome:** Apps can safely add behavior, automation, and integrations.

- Finish App-owned view configuration/plugin loading through a public frontend
  contract.
  *(Phase One: declarative composites on Core palette. Signed FE modules later.)*
- Harden trusted ToolContext hooks, retry/idempotency semantics, job scheduling,
  and human approval paths. *(Later wave.)*
- Publish connector SDK/runtime conventions for OAuth, secret isolation,
  sync/cursor behavior, deduplication, conflict resolution, webhooks, and
  staged outbound writes. *(Later wave.)*
- Add constrained graph-query and retrieval tools so agents can reason across
  App boundaries without raw graph access. *(Later wave.)*
- Complete scoped agent memory and explicit scratch-to-domain promotion.
  *(Later wave.)*

**Exit test:** A package can mirror an external source, show freshness and
provenance, propose an action, and execute it only after the same policy and
approval controls used by Core.

### F3 — SaaS commerce, entitlement, and deployment

**Status (2026-09-15):** **Phase One (manual entitlement kill-switch) shipped**
— unlocks foundation completion test 5's entitlement half. Stripe checkout,
usage meters, BillingAccount, and customer portal remain later waves.

**Phase One ships:** `Entitlement` Object (I-GRAPH-02) with workspace + key
unique index; `grant` / `revoke` / `list` admin endpoints; commercial
`package.class: commercial_app` gated at `install_app` / `resume_app`; revoke
→ `pause_app` with `data_access=core_generic_read` and
`retention=retain_until_uninstall`. Contract package
`examples/reference-commercial-hello` +
`tests/contract/test_f3_entitlement_kill_switch.py` prove deny→grant→install→
revoke→pause while sibling community App and Entry reads stay healthy.

**Outcome:** Integral can sell hosted Core capabilities and commercial Apps
without compromising the open-core boundary.

- Introduce provider-neutral `Subscription`, `Entitlement`, `UsageMeter`,
  `UsageLedgerEvent`, and `BillingAccount` concepts. A Billing Account may own
  one or more workspaces; each workspace belongs to one Billing Account. A
  personal workspace receives a personal account by default, while an
  organization can place production, sandbox, and client workspaces under one
  commercial account.
  *(Phase One: `Entitlement` projection + manual grant/revoke only.)*
- Use Stripe Billing for checkout, recurring subscriptions, invoice/payment
  state, customer self-service portal, and usage-based charges where the
  pricing model calls for them.
  *(Later wave.)*
- Treat Stripe as the financial-system source of truth and keep an Integral
  entitlement projection for fast, auditable authorization decisions.
- Process signed Stripe webhooks idempotently through a durable inbox/outbox;
  reconcile periodically rather than trusting browser redirects or a single
  webhook delivery.
- Record product consumption first in Integral's immutable usage ledger, then
  report eligible aggregated meter events to Stripe. The ledger, not a browser
  request or an individual App call, is the source for quota enforcement,
  customer explanation, retry de-duplication, and billing reconciliation.
- Enforce entitlements server-side at package install, premium capability use,
  connector activation, and metered action boundaries. The frontend explains
  access; it is never the gate.
  *(Phase One: install + resume gated; revoke pauses commercial Apps.)*
- Define trials, grace periods, failed-payment behavior, downgrades, data
  export, read-only access, retention, and commercial-App disablement before
  charging the first customer.
- Add Stripe customer-portal entry points, billing-admin roles, usage/limit
  visibility, invoices, and support diagnostics.
- Support audited, expiring manual entitlements for trials, enterprise
  contracts, migration windows, support recovery, and credits without
  requiring support staff to mutate a customer's Stripe subscription.
  *(Phase One: manual grant/revoke with optional `expires_at`; audit via
  existing ChangeEvent / lifecycle pause.)*

Stripe supports recurring and usage-based Billing, a self-service customer
portal, and webhook-driven subscription state handling; the design above uses
those mechanisms while keeping entitlement logic inside Integral.
[Stripe Billing overview](https://docs.stripe.com/billing) and
[subscription webhook guidance](https://docs.stripe.com/billing/subscriptions/webhooks)
support this approach.

**Initial metering candidates:** active seats, storage, enabled connectors,
and premium-App seats. Meter only values that are product-legible,
independently auditable, and difficult to game. Start with plans plus a small
number of meters; defer agent tokens and connector-sync volume until their
cost, retry, BYOK, and customer-explanation semantics are mature. Every meter
defines its window, reset, hard/soft limit, overage, retry, and failed-work
semantics before sale.

**Exit test:** A workspace administrator can start a trial, purchase a plan or
commercial App, manage it in the customer portal, observe its limits, and
receive correct server-side access changes after Stripe subscription events.

### F4 — Reference Apps and proven vertical loops

**Outcome:** Commercial Apps prove that the extension boundary works in real
domains.

- Build Organization as the shared legal-entity master-data App.
- Evolve HRM and Payroll as independently packaged Apps that consume the
  Organization contract without direct Core or one-another coupling.
- Complete one work-system connector and one communications connector from
  authorization through trustworthy, source-cited resident answers.
- Use the flagship workspace as a repeatable demo, onboarding path, UAT suite,
  performance fixture, and design-partner environment.

**Exit test:** A customer can deploy the flagship workspace from a Core-only
SaaS tenant, install entitled Apps, connect approved systems, ask a grounded
question, approve a staged action, and inspect its result and audit trail.

### F5 — Cohesive experience and v1.0 operations

**Outcome:** The foundation feels like one understandable product.

- Provide a guided organization onboarding path, App catalog, entitlement
  explanations, resident inbox, approvals, and governance dashboard.
- Complete responsive-web parity, deployment automation, backup/restore,
  tenant isolation, support runbooks, public API/extension documentation, and
  an enterprise/self-hosted posture.
- Validate commercial packaging, upgrade, billing, cancellation, data export,
  and support workflows with design partners before v1.0.

## Design rules for SaaS and open core

1. **Entitlements authorize capabilities, not imports.** Core never imports a
   paid App to decide whether it can run. Catalog and runtime capability checks
   resolve entitlements through one service.
2. **The workspace/customer organization is the commercial subject.** Users
   receive roles within it; billing ownership and product entitlements belong
   to it unless a personal plan deliberately says otherwise.
3. **Stripe events are asynchronous and untrusted until verified.** Verify
   signatures, deduplicate event IDs, persist processing state, and reconcile.
4. **Paid does not mean opaque.** Package metadata, requested permissions,
   capabilities, data retention behavior, and uninstall effects are visible to
   administrators before install.
5. **A lapsed entitlement is a governed state.** Define read-only access,
   export rights, grace period, deletion/retention timing, and recovery rather
   than silently removing customer data.
6. **Core APIs are public contracts; Core internals are not.** Every supported
   extension point is versioned and tested. Any required private import is a
   missing contract, not an exception.
7. **Commercial state is recoverable and explainable.** A billing administrator
   can see the Billing Account, plan, entitlement source, usage, limit, grace
   state, and manual override that determined an access decision.

## Decisions still required

- The open-source license and commercial-package distribution/license terms.
- Whether commercial Apps are hosted catalog artifacts, private package
  registries, encrypted bundles, or a combination.
- Which entitlements are plan limits versus billable metered usage.
- Whether self-hosted customers may use commercial Apps and how license
  verification works in disconnected environments.
- The supported frontend-plugin security model: sandboxed iframe, signed
  module, or declarative-only first release.
- Data-retention and export commitments after cancellation, per plan and
  jurisdiction.
- Publisher onboarding, package review, vulnerability disclosure, signing-key
  rotation, and revocation policy.
- The definitive data-residency, encryption, backup, and tenant-isolation
  commitments for hosted plans.

These choices should be settled before implementation of F3, because they
shape the public extension and customer contract.
