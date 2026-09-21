# ADR-013: Operational Model contract and hard cutover

**Status:** Accepted
**Date:** 2026-09-21
**Decider:** Eldon Marks

## Context

Integral is pre-production. Its former model vocabulary had become an obstacle
to building a coherent public substrate: one term ambiguously
referred to a domain model, a catalog item, an attached instance, and an App
package. Retaining aliases would preserve that ambiguity in every future SDK,
API, skill, manifest, and persistence contract.

## Decision

Integral uses **Operational Model** as the single contract term for the live,
declarative model of an App or Track.

| Contract concern | Canonical contract |
| --- | --- |
| Persistence | `OperationalModel` and `OperationalModels` graph nodes; `HAS_OPERATIONAL_MODEL` edge |
| REST | `/api/operational-models` |
| Identifiers | `operational_model_id`, `attached_operational_model_id`, `library_operational_model_id` |
| Package manifest | `operational-model.yaml` with `integral_operational_model_version` |
| Built-in package root | `backend/app/packages/` |
| Signature configuration | `INTEGRAL_OPERATIONAL_MODEL_PUBKEY` |
| Resident tools and skills | `integral_*_model*` and `integral_models` |
| User routes | `/models` and `/models/:id` |

An **App Package** is an immutable distributable containing an App Model and
optional approved assets or code. A **Model Listing** is a discoverable catalog
record. An **Installed App** is a workspace-local materialization. Those are
separate contracts and must not be named Operational Model.

There are no legacy model aliases. Existing development databases and packages
are deliberately incompatible and must be recreated against this contract.

An Agent Profile remains a separate resident-configuration concept. It is not
an Operational Model and is outside this rename.

## Alternatives considered

| Alternative | Rejected because |
| --- | --- |
| Compatibility aliases | They preserve conflicting concepts and multiply every future contract. |
| Deferred storage/API migration | There are no production consumers or data-retention obligations that justify the ongoing cost. |
| Schema | It excludes views, governed operations, guidance, and model lifecycle. |
| Template | It implies one-time copying rather than a live, revisioned specification. |

## Consequences

- Developers have one consistent term from package manifest through API, SDK,
  resident skill, and UI.
- Development environments must use fresh data and rebuilt packages after the
  cutover.
- The project may not introduce a legacy alias without a new ADR and an
  explicit compatibility consumer.
- Technical documentation now lives under `docs/operational-models/`.

## Follow-up work

1. Qualify the cold-cutover contract through Core-only, external App,
   Postgres, browser, and clean-install evidence.
2. Reauthor system skills and reference packages against the new manifest and
   tool names.
3. Continue the information and projection contract work from the Core
   acceptance ledger.
