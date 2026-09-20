# WP-04 Application-Definition Contract

## Implemented foundation

An installed App has an immutable, compiler-validated `ApplicationDefinition`
revision. The new node is structurally attached through
`App —HAS_APPLICATION_DEFINITION→ ApplicationDefinition`; the App keeps only
the `active_definition_id` and `active_definition_revision` fast-paths.

Each revision stores the canonical Content Profile manifest, stable manifest
fingerprint, package provenance, base definition identity, local-override
envelope, and a requirement ledger. The ledger names package, required App
dependencies, tracks/templates, commands, queries, skills and agents that the
revision promises to materialize.

Installing a package creates revision 1 before profile materialization.
Updating from its library compiles a new revision after the update succeeds;
the earlier revision remains available with `status="superseded"`. Repeating
an equal compiler result reuses the active revision, so retries do not mint
duplicate definition records.

Blank Apps follow the same seam: their attached default Content Profile
compiles into an initial `source_kind="local"` definition immediately after
the App is rooted and catalogued. A greenfield proposal and a package install
therefore have the same effective-contract authority from their first write.

`preview_application_definition` provides the corresponding review payload:
the raw structural diff plus business labels, planned materialization effects,
an explicit affected-records status, and limitations. It never calls an
unevaluated migration impact “zero affected records,” and it does not confer
authorization or apply an effect.

## Authority boundary

Content Profiles continue to own field, view and composition compilation.
ApplicationDefinition owns the effective installed contract and the evidence
needed to explain it. The lifecycle never executes arbitrary generated Python:
only compiler-supported manifest capabilities appear in the canonical snapshot.

## Follow-on work

This establishes the durable revision seam. The remainder of WP-04 will bind
approval records and work plans to a definition revision, expose readable
semantic preview/diff endpoints, execute installs/upgrades through the durable
work kernel, add verified ledger completion evidence, and implement explicit
three-way package/local merge and incompatible-migration controls.
