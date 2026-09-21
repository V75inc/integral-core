# WP-04 Application-Definition Contract

## Implemented foundation

An installed App has an immutable, compiler-validated `ApplicationDefinition`
revision. The new node is structurally attached through
`App —HAS_APPLICATION_DEFINITION→ ApplicationDefinition`; the App keeps only
the `active_definition_id` and `active_definition_revision` fast-paths.

Each revision stores the canonical Operational Model manifest, stable manifest
fingerprint, package provenance, base definition identity, local-override
envelope, and a requirement ledger. The ledger names package, required App
dependencies, tracks/templates, commands, queries, skills and agents that the
revision promises to materialize.

Package-backed revisions also retain their immutable canonical package-base
manifest. When the effective installed contract differs, `local_overrides`
records base and effective fingerprints plus a structural diff. Those inputs
drive the three-way conflict planner; Core reports divergent package/local
paths and refuses to choose a resolution automatically.

Installing a package creates revision 1 before profile materialization.
Updating from its library compiles a new revision after the update succeeds;
the earlier revision remains available with `status="superseded"`. Repeating
an equal effective contract **and package base** reuses the active revision,
so retries do not mint duplicate definition records. A changed package base
always appends a revision, even when tenant-local choices keep the effective
contract equal.

Blank Apps follow the same seam: their attached default Operational Model
compiles into an initial `source_kind="local"` definition immediately after
the App is rooted and catalogued. A greenfield proposal and a package install
therefore have the same effective-contract authority from their first write.

`preview_application_definition` provides the corresponding review payload:
the raw structural diff plus business labels, planned materialization effects,
an explicit affected-records status, and limitations. It never calls an
unevaluated migration impact “zero affected records,” and it does not confer
authorization or apply an effect.

The active contract is available to authenticated App readers at
`GET /api/apps/{app_id}/definition`. This is the read boundary future
authoring, approval and worker paths use instead of treating a mutable
Operational Model as the installed App's execution authority.

`GET /api/apps/{app_id}/definition/preview` compares that immutable active
definition with the App's currently attached Operational Model. It is an
App-read-authorized, read-only review boundary: callers receive the semantic
preview without creating a revision, changing a profile, or applying a
migration.

The package-apply preview also includes a three-way assessment when the active
definition has an immutable package base. It compares package base, active
effective contract, and incoming package contract. Upstream-only and
local-only changes are reported as non-conflicting; simultaneous divergent
changes are returned with their manifest paths and values. The assessment is
read-only and never selects a resolution or applies an upgrade.
Lifecycle upgrades and explicit package applies reject those simultaneous
changes with a structured 409 before mutating the attached Operational Model. A caller
must publish a resolved definition revision and retry; Core does not silently
choose package or tenant state.

Package-apply previews also expose `migration_safety`. Core calculates the
same effective post-merge manifest it would persist, including tenant-owned
additions and package dependencies, then measures that schema against records
already stored in the attached App. An update is rejected with a structured
422 when those records would fail validation or require migration and the
effective package manifest declares no `migrations[].ops[]`. This guard runs
for lifecycle upgrades, explicit merges, and applies before any profile,
materialization, or operational-layer mutation. A declared migration operation
permits the update. After the new definition and materialization evidence are
persisted, Core starts the existing per-entry migration tracker against the
same effective manifest. The update response includes `migration_tracker`:
`not_needed` for unaffected data, or `running` after affected entries have
been synchronously marked pending. The runner records final per-entry and
profile-level status independently, so a long migration never disguises an
upgrade as already complete.

App-bound WorkItems resolve and persist the active definition ID at enqueue.
They reject a supplied stale revision, a cross-workspace App, and a definition
without an App. The effect boundary rechecks that the revision is still active.
When work pauses for a human decision, `WorkApproval` snapshots the same
definition ID, making the approval auditable against the contract reviewed.

Package admission consumes the loader's durable trust verdict. An artifact
whose metadata records `signature_verified: false` is rejected before install,
lifecycle upgrade, explicit library merge, or explicit apply. Legacy catalog
rows without signature metadata remain compatible; a known failed verification
is never treated as informational.
When catalog metadata includes a currently available bundle directory and its
validated fingerprint, admission recomputes that fingerprint and refuses
activation on drift. A catalog restored without its source directory continues
to use its recorded artifact identity; Core does not pretend it can verify a
file it cannot read.

Opting into package seed data makes both manifest seeds and a present
`seeds/post_install.py` transactional installation prerequisites. A post-seed
failure now propagates into the install saga so compensation can remove partial
state; Core does not transition the App to active while requested seed data is
missing.

After an App becomes active, Core records `materialization_evidence` on its
definition. Package source and App-track requirements are verified against
persisted nodes, as are declared App Skills. Requirement kinds without a
generic Core verifier are marked `not_evaluated` with an explanation. App
Agents are verified by their App-bound `AgentConfig` and manifest agent key.
Commands and declared queries are verified against their per-workspace App
registries. Materialized EntryTypes and Views are verified within their
definition-resolved App track. Required App dependencies are verified against
active, version-compatible App installs in the same workspace and record the
concrete App identity that satisfies each dependency. The evidence is intentionally
conservative: a missing or unevaluated row never means the requirement was
completed.

`POST /api/apps/{app_id}/definition/verify` is the explicit App-update-authorized
refresh boundary. It rechecks and persists the active revision's evidence after
runtime changes without changing the definition revision. Definition reads stay
side-effect-free.

An explicit library merge or apply appends a definition from the **merged
attached Operational Model**, not from the raw library manifest. This preserves tenant
customizations in the effective contract. The response returns the new
definition ID and revision; a library upgrade follows the same rule.

## Authority boundary

Operational Models continue to own field, view and composition compilation.
ApplicationDefinition owns the effective installed contract and the evidence
needed to explain it. The lifecycle never executes arbitrary generated Python:
only compiler-supported manifest capabilities appear in the canonical snapshot.
Runtime extension-view resolution reads the active definition, rather than the
mutable attached Operational Model, so a draft authoring change cannot alter a live App
surface before it is represented by an authorized revision.
Restart rehydration follows the same authority: hook, tool and operation
registrations are rebuilt from the active definition, with the attached Operational Model
retained only as a legacy fallback when no definition exists.
Run capability snapshots also use the active definition so their durable audit
record describes the executable contract, not a pending profile edit.
The agent staging-exemption decision reads `unstaged_tracks` from that same
active contract; an unactivated profile edit cannot silently bypass review.
Resume registration, dependency-aware uninstall checks, relation-uninstall
policy resolution, and bundle teardown identity also resolve from the active
definition before falling back for legacy Apps.
Anchored-track runtime refresh uses the active definition's template catalogue,
so a profile draft cannot rewrite shared template materialization during reads.

## Follow-on work

This establishes the durable revision seam. The remainder of WP-04 will
execute installs/upgrades through the durable work kernel, add verifiers for
skills, agents, commands and queries, and deepen migration execution from the
current declared-operation guard into per-field coverage and transactional
transform reporting.
