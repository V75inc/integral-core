# App package architecture

An App package is a reusable definition of operational structure and behavior. Its directory contains `operational-model.yaml` and any declared skills, tools, views, assets, or supporting resources. The directory name equals `package.slug`.

## Formats and identity

Use authoring YAML version 3. The loader assembles canonical runtime schema version 2. Package class identifies distribution purpose: `core_package`, `community_app`, `verified_app`, `commercial_app`, or `private_org_app`. Trust tier is a separate execution decision.

The loader records manifest and bundle fingerprints, load issues, skill files, Python presence, and signature results. Python caches and the signature file are excluded from the deterministic content fingerprint. A library entry makes a package discoverable; installation materializes its workspace instance.

## Declared parts

Tracks and EntryTypes describe records. View declarations and composite types describe presentation. Queries expose bounded information contracts. Operations declare input, policy action, staging, and implementation. Tools supply reviewed execution through the facade. Hooks bind declared behavior to cataloged substrate events. Skills supply instructions and do not grant authority.

Settings can pause installation until required values are provided. Dependencies constrain installation and removal. Cross-App references retain permission checks and remain inside the workspace contract.

## Active definition

Each lifecycle-materialized App has an active immutable ApplicationDefinition revision. It carries the compiled manifest, requirement ledger, and provenance. `HAS_APPLICATION_DEFINITION` is authority; scalar active pointers must agree. A changed contract creates another revision.

Attached OperationalModels remain the schema/composition component. Capabilities needing an effective application contract resolve the active definition rather than authorizing from a library file or draft.

## Lifecycle

States include installing, awaiting_settings, active, paused, uninstalled, and failed. Installation uses recorded compensation for partial steps; failed compensations must not prevent earlier cleanup. Signed setting-completion tokens and abandonment cleanup protect paused installations.

Pause withdraws active behavior as required. Uninstall checks dependencies and references; a forced path is explicitly destructive and emits its own audit action. Upgrades consider schema, definitions, requirements, and instance customizations rather than blindly replacing all state.

## Boundary

Bundles never import private Core services or models into tools. They use ToolContext and published contracts. Tool/hook registrations are workspace/package owned, collision-checked, and removed through lifecycle. Skill overlays include active accessible Apps only.

Production admission of Python depends on trust tier and configured signatures. The presence of an allowed manifest cannot exempt code from permission, schema, transaction, or effect controls.

Use the [quickstart](../developer/quickstart.md), [extension contract](../platform/extension-contract-v1.md), and [signing runbook](../ops/OPERATIONAL_MODEL_SIGNING.md). The filename is retained for link stability; it is not a requirement to author obsolete YAML.
