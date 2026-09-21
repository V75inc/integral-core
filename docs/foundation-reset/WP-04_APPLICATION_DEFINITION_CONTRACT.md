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

Package-backed revisions also retain their immutable canonical package-base
manifest. When the effective installed contract differs, `local_overrides`
records base and effective fingerprints plus a structural diff. This supplies
the durable inputs for a later three-way conflict planner; it does not yet
choose or apply conflict resolutions automatically.

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

The active contract is available to authenticated App readers at
`GET /api/apps/{app_id}/definition`. This is the read boundary future
authoring, approval and worker paths use instead of treating a mutable
Content Profile as the installed App's execution authority.

`GET /api/apps/{app_id}/definition/preview` compares that immutable active
definition with the App's currently attached Content Profile. It is an
App-read-authorized, read-only review boundary: callers receive the semantic
preview without creating a revision, changing a profile, or applying a
migration.

App-bound WorkItems resolve and persist the active definition ID at enqueue.
They reject a supplied stale revision, a cross-workspace App, and a definition
without an App. The effect boundary rechecks that the revision is still active.
When work pauses for a human decision, `WorkApproval` snapshots the same
definition ID, making the approval auditable against the contract reviewed.

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
attached profile**, not from the raw library manifest. This preserves tenant
customizations in the effective contract. The response returns the new
definition ID and revision; a library upgrade follows the same rule.

## Authority boundary

Content Profiles continue to own field, view and composition compilation.
ApplicationDefinition owns the effective installed contract and the evidence
needed to explain it. The lifecycle never executes arbitrary generated Python:
only compiler-supported manifest capabilities appear in the canonical snapshot.
Runtime extension-view resolution reads the active definition, rather than the
mutable attached profile, so a draft authoring change cannot alter a live App
surface before it is represented by an authorized revision.
Restart rehydration follows the same authority: hook, tool and operation
registrations are rebuilt from the active definition, with the attached profile
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
skills, agents, commands and queries, and implement explicit three-way
package/local merge and incompatible-migration controls.
