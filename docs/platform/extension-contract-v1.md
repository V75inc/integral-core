# The Integral App extension contract

This contract defines how domain Apps use Core. The versioned filename denotes the extension boundary; it does not mean App authoring YAML is version 1. Current authoring YAML is version 3 and runtime manifests are schema version 2.

## Core responsibilities

Core owns authenticated identity, workspace scope, resource access, schema validation, graph integrity, generic lifecycle, execution controls, transcripts, and receipts. Apps supply domain meaning through declarations and reviewed implementations. Pricing, checkout, hosting, and commercial account rules remain in the host or App.

Core must not import `app.packages` or `app.plugins` from services, APIs, models, or schemas. It must not branch on App slug, EntryType name, or Track identity. Core-only boot must work without domain packages.

## Information and capabilities

The preferred facade supplies `get(object_ref)`, `query(query_spec)`, and `invoke(operation_key, payload)`. Query modes are `declared_capability` and `core_open`; mode selects a contract, never a permission bypass. Field and revision contracts distinguish base values, custom fields, relation definitions, and record/schema revisions.

OperationContext is injected by Core. Its identity includes principal, workspace, scope, bundle slug, installed App, operation key, and optional idempotency/correlation keys. It offers validated create/update helpers, conditional updates, audit emission, deduplicated notifications, and the preferred facade. Raw private Core imports are forbidden.

## Declaration requirements

Packages declare input/output schemas, operation policy and staging behavior, query shape, tools, hooks, skills, and presentation assets. Python implementations require their trust tier and signature posture. An installed App resolves the immutable active ApplicationDefinition before using its effective capability contract.

The frozen hook catalog contains `entry.transform`, `entry.public_share`, `entry.precompute`, `entry.create`, `entry.validate`, `entry.update`, `connector.dedup`, `connector.auto_link`, and `email.sent`. A new point requires a reviewed decision and invariant update. Registration collisions fail rather than silently replacing another package's tool.

## Presentation

App-owned declarative composites use registered Core primitives. Sandboxed iframe extension views use declared assets, hosted paths, and the view handshake. This is an implemented extension surface; arbitrary remote React plugins or inline executable manifest code are not substitutes.

Package App Home uses declared queries/actions under the active definition. Dashboards are instance projections. Unavailable data must remain distinguishable from empty results; action preparation does not silently submit a chat turn.

## Evidence

Use `examples/reference-hello-app/` and `backend/tests/contract/` for executable examples. Test Core-only isolation, input validation, current permissions, stale revisions, capability withdrawal, signature rejection, and browser readback. The [governance guide](extension-contract-governance.md) explains contract changes.
