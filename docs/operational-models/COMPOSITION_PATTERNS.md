# Compose operational knowledge

Choose composition from identity, lifecycle, and audience. Visual depth alone is not a reason to create another containment hierarchy.

## Sibling collections

Use sibling Tracks for independent collections: requests, decisions, evidence, or deliveries. Relate Entries through declared relation fields. Each collection keeps its own schema, views, permissions, and lifecycle.

A relationship can span supported Tracks while preserving authorized destination resolution. Cross-App references follow the same-workspace contract. A reference does not make its destination readable.

## Anchored collections

Use a relation targeting a Track template when an Entry should gain a related collection. `materialize_anchor_track` provisions the Track in the source workspace, and `_sync_anchor_edges` is the sole anchor edge-writing path.

Template-provisioned anchors share the template OperationalModel by reference in the current contract. The scalar attached-model pointer and `HAS_OPERATIONAL_MODEL` target must agree. `TEMPLATED_FROM` records model-template provenance; `USES_TEMPLATE` retains its distinct Track-to-Track meaning.

The Entry does not contain the Track. ANCHORS is an additional relationship; actual Track containment remains in the rooted workspace/App structure. Cross-workspace anchoring is unsupported.

Deleting an anchor Entry can trigger policy-governed hard cascade over anchored Tracks. Inspect this behavior before using an anchor where the related collection must outlive the source. Denial preserves the Track and records the denial; an archive grace period is not part of the current anchor contract.

## Audience boundaries

Put restricted information in a separately governed Track or Entry. Exclusions affect inherited access and do not cancel direct grants. An authorized parent can render a restricted destination without exposing the destination's fields.

Avoid hidden-field privacy, Entry-contains-Entry, Entry-contains-Track, Track-contains-Track, and growing JSON arrays of operational entities. A JSON value is appropriate for a value-shaped payload, not a concealed permission-bearing subsystem.

## Review questions

Identify who owns each record, which audience can read it, whether it has an independent lifecycle, how deletion behaves, and which relationship carries the meaning. Then choose the simplest declared structure satisfying those requirements.
