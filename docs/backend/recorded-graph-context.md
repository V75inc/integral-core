# Recorded graph context

`POST /api/graph-context` and `integral_get_graph_context` accept
`detail: "recorded"` with the same authenticated workspace binding as the
summary view. Summary remains the default, preserving format v1. Recorded
context uses JV Spatial format v2, including field states, recorded evidence,
coverage and qualified relationship definitions. It does not assess decision
sufficiency or independently verify the values. `status: active` is Integral's
lifecycle state, not workflow completion.

## Operational Model declaration

An EntryType's normalized `form_schema.graph_context` selects visible declared
scalar custom fields and names visible declared relation fields. The declaration
is opt-in and must specify the `schema_revision` of the Entry records it applies
to. Keep it aligned with the published profile version's record schema revision;
older records do not acquire new fields or meanings merely because their type's
schema changed. The declaration travels through form-schema normalization and
manifest reconciliation when nested inside `form_schema`.

```json
{
  "fields": [
    {"key": "state", "type": "text"},
    {"key": "observed", "type": "date"},
    {"key": "verification", "type": "text"},
    {"key": "prerequisite", "type": "relation"}
  ],
  "graph_context": {
    "schema_revision": 1,
    "fields": {
      "state": {"label": "Recorded state", "observed_at": "observed", "verification": "verification"},
      "observed": {},
      "verification": {}
    },
    "relations": {
      "prerequisite": {
        "description": "Source requires the recorded target before proceeding.",
        "direction": "requires",
        "inverse": "required by",
        "conditions": "The link does not certify completion."
      }
    }
  }
}
```

Field hints accept label, description and unit. Evidence bindings accept source,
observed_at, verification and confidence **keys**, and each binding must itself
be a selected scalar field. Values are recorded assertions. Core adds the
record revision and generic origin class; it does not expose opaque actor/source
IDs or interpret provenance synchronization timestamps as observation dates.
Hidden fields, body text, containers, computed values, arbitrary dotted paths
and undeclared fields cannot be selected. Relation state remains on REFERENCES.
Meanings match its field_key and relation_type plus the source's concrete type,
track and schema revision; matching a field name alone supplies no meaning.

## Access and bounds

Both captures use native bounded JV Spatial traversal, applying current user
policy before expansion. The first discovers types only from represented
readable entries; at most eight type records are batch-loaded. The second
rechecks policy with a fresh permission cache, including type-schema access.
Both traversals share the request's candidate limit and a three-second host
deadline. Type reads are additionally capped at eight; the candidate limit is
not a physical database row-scan guarantee. At most twelve distinct custom
paths and sixty-four relation declarations may participate; broader profiles
require a narrower root. The output retains the requested byte/topology caps.

Each record receives only its own type/revision/track's field grants. Shared
paths with conflicting hints fall back to neutral metadata and generic origin
rather than attributing another type's units or evidence. Newly encountered or
undeclared types receive basic recorded context only. Therefore complete
**selected** detail is not complete business knowledge. Absent declarations,
missing/null values, unresolved meanings and truncated captures remain unknown.

This field selection controls context relevance; it adds no field-level privacy
primitive (I-ACCESS-01). Differently governed data belongs in separately governed
resources. Hidden UI fields are excluded from context but are not an access boundary.

No cross-workspace relationships, denied intermediaries, inaccessible values,
installed-App entry bypasses or host-only work statistics are exposed. Captures
are immutable best-effort multi-read representations, not atomic snapshots.
The language model remains downstream of the data layer.
