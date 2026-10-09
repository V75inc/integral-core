# Package and migration responsibilities

Keep disk package, catalog model, attached model, App instance, and active definition distinct. Disk YAML v3 compiles to runtime schema v2; an installed App's immutable active definition establishes the effective application contract.

Schema changes use draft validation, impact checks, and supported declarative migration operations. A forced publish is separately authorized and does not migrate existing records. Runtime migration work has durable identity, progress, and failed-item diagnostics.

Use the [package architecture](app-bundles-v1.md) for lifecycle, [migrations](../operational-models/MIGRATIONS.md) for data changes, and [signing](../ops/OPERATIONAL_MODEL_SIGNING.md) for executable package admission.
