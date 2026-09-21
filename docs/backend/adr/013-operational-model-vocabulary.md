# ADR-013: Operational Model vocabulary and compatibility migration

**Status:** Accepted
**Date:** 2026-09-21
**Decider:** Eldon Marks

## Context

Integral’s public term **Content Profile** has accumulated four distinct
meanings: a declarative Track/App definition, its workspace-attached instance,
a library/catalog record, and a package artifact. The persistence node and
legacy API namespace compound that ambiguity. Authors then have to translate a
business need into unfamiliar terms before they can understand whether they are
creating an App, a package, a schema, or a model revision.

The public App extension contract already distinguishes a package artifact from
an installed App instance. That distinction must extend to the model that
shapes the App, without breaking existing APIs, stored nodes, package manifests,
or external integrations.

## Decision

**Operational Model** is the canonical product, authoring, and documentation
term for Integral’s declarative model of an operational domain.

An Operational Model can be App-scoped or Track-scoped. It declares records,
relationships, views, operational rules, and agent guidance. It is not an
immutable distributable and it is not a workspace-local installation.

| Canonical term | Meaning |
| --- | --- |
| Operational Model | Declarative model of an App or Track |
| App Model | An App-scoped Operational Model |
| Track Model | A Track-scoped Operational Model |
| Model Revision | Draft or published revision of an Operational Model |
| App Package | Immutable, versioned, distributable artifact containing an App Model and optional approved assets/code |
| Model Listing | Catalog record describing an App Package or reusable Track Model |
| Installed App | Workspace-local materialization of an App Package |

`ContentProfile`, `content_profile`, and `/content-profiles` remain
compatibility identifiers in the current persistence model, package loader, and
REST API. They are not the preferred product vocabulary.

The web workspace uses `/models` as the canonical user-facing route and keeps
`/content-profiles` available for existing bookmarks. No REST endpoint is
renamed in this decision. A later API versioning decision may add `/models`
aliases with explicit deprecation headers after external-client inventory.

## Alternatives considered

| Alternative | Rejected because |
| --- | --- |
| Keep Content Profile | It does not distinguish a model from a catalog package or installed App, and “profile” implies a user preference rather than an operational structure. |
| Template | A model is live, versioned, and evolves after it is installed; “template” implies one-time copying. |
| Schema | Too narrow: views, skills, operations, and lifecycle rules are part of the model. |
| Blueprint | Friendly but less precise for revisions, migrations, and API documentation. |
| Rename storage and APIs immediately | Breaks external clients and adds migration risk without improving the user experience sooner. |

## Consequences

- Product UI, onboarding, and primary documentation use **Operational Model**.
- The documentation has one conceptual entry point, while existing
  `content-profiles/` material becomes implementation/reference detail.
- App authors distinguish clearly between a model, package, catalog listing,
  and installed App.
- Internal code and API contracts preserve compatibility until an intentional
  versioned migration.

## Follow-up work

1. Add model terminology to UI labels and canonical `/models` links.
2. Consolidate primary model documentation under `docs/operational-models/`.
3. Update extension examples and package metadata language to distinguish
   App Packages from Operational Models.
4. Inventory public REST/MCP/SDK identifiers before proposing aliases or a
   versioned API migration.
