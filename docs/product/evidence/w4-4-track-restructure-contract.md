# W4.4 Track restructuring contract

Status: implementation contract for `integral_merge_tracks` and
`integral_split_track`.

## Shared guarantees

- Every operation is staged with a value-free preview bound to the active
  principal, workspace, selected record ids and revisions, Track schema
  revisions, taxonomy, and explicit mappings.
- The executor reruns the full preflight immediately before writing. A changed
  Entry, schema, taxonomy, membership set, or access decision makes the stage
  stale and refuses the operation.
- The full write, including source retirement or destination Track creation,
  runs in one supported graph transaction. A store without transaction support
  refuses before its first write.
- Each set is capped at 500 Entries. The preview stops after the 501st result
  and refuses larger operations rather than hydrating an unbounded set.
- Workspace and container scope are invariant: source and destination remain
  in the same Workspace and under the same App, or both remain standalone.
- `CONTAINS`, `IS_OF_TYPE`, `TAGGED_WITH`, `REFERENCES`, `ANCHORS`, and
  `HAS_MEMBER_REF` stay consistent with the new Track. Typed relation metadata
  is preserved and cross-Track/cross-App flags are recalculated.
- Any unrecognized data-bearing sidecar or unsupported access edge causes a
  named refusal. No best-effort partial migration is permitted.

## Merge Tracks

Inputs are source and target Track ids, a complete EntryType mapping, a
complete per-EntryType custom-field mapping, a complete source-tag-to-target-tag
mapping, and explicit View name mappings where source and target names collide.

Preflight requires:

- distinct Tracks in the same Workspace and same App/standalone container;
- update/delete authority on the source and update/create authority on the
  target, plus edit authority for every affected Entry;
- writable source and target schemas and a valid destination for every EntryType,
  field value, tag, and View;
- no source Track access grants, exclusions, invitations, share links, anchored
  child Tracks, or unknown sidecars that cannot be transferred without changing
  their security or navigation meaning;
- every source Entry (up to 500) validates against the mapped destination
  schema, tag taxonomy, and relation contract.

Apply reuses W4.3's relation-preserving Entry reparenting core. It transfers
compatible Views into the target operational model, removes source View and
taxonomy nodes only after their replacements exist, then retires the now-empty
source Track and its private schema subtree. Any deletion/cleanup error aborts
and rolls back the complete graph transaction. A single audit receipt records
the source, target, affected Entry ids, and mappings; it does not include Entry
values.

## Split Track

Inputs are a source Track, new Track title, and exactly one selector: one or
more EntryType keys or a W3.8 canonical filter list. The split clones the
source's effective EntryType definitions and taxonomy into a new Track in the
same Workspace and App/standalone container. The caller may provide a partial
field/EntryType mapping only when intentionally changing the cloned schema;
identity mapping is generated for an exact clone.

Preflight requires:

- source update and destination-create authority, writable source schema, and
  edit authority for every selected Entry;
- 1–500 selected Entries, every selector resolved against the source schema,
  and every selected Entry validated against the destination clone;
- tag taxonomy, Views, and relation fields cloned with stable keys; existing
  relation edges remain attached to their original Entry targets and metadata
  is recalculated after movement;
- a unique destination Track title and no source-level access sidecars whose
  intent cannot be preserved.

Apply creates the destination Track, operational model, EntryTypes, tags, and
Views and moves the selected Entries within the same transaction. The source
Track and its schema remain in place for Entries not selected. Failure at any
step removes the new graph subgraph and leaves all source Entries unchanged.

## Evidence required to close W4.4

- merge and split previews refuse cross-Workspace/cross-App sets, stale
  revisions, incomplete mappings, permission changes, oversized sets, and
  unsupported sidecars before writes;
- exact Entry ids, EntryType values, custom-field values, tag membership,
  View behavior, comments, attachments, and relation edges match independent
  expected results after each successful operation;
- PostgreSQL rollback tests inject failures after Entry movement, View
  transfer, and source retirement/destination creation, proving no partial
  graph is committed;
- corpus scenarios cover same-App, standalone, empty, mixed EntryType, duplicate
  tag membership, inbound/outbound relation, and collision cases.

## Qualification ledger

The current W4.4 branch has focused deterministic coverage for:

- same-App merge with mixed EntryTypes, renamed fields, Track tags, and Views;
- inbound and outbound REFERENCES preservation with `cross_track` recalculated,
  plus Entry comments and attachments retained after the merge;
- cross-Workspace and cross-App merge refusal, 501-entry merge/split refusal,
  collaborator sidecar refusal on merge and split, stale preview refusal after
  an Entry revision or source permission changes, and explicit View-name
  collision resolution;
- empty standalone merge preview and duplicate source/target tag membership
  deduplication while preserving the existing target tag edge metadata.

PostgreSQL-only rollback tests remain separately gated by `INTEGRAL_TEST_DB=postgres`.
The broader corpus and real-PostgreSQL execution still need to pass before W4.4
can close; these focused fixtures do not claim exhaustive sidecar, relation, or
cross-container coverage.
