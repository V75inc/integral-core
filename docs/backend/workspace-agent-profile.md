# Workspace-scoped resident skill overlay

The resident's available App skills depend on the authenticated principal and active workspace. Include only installed active Apps on which the user has an effective role. Workspace membership alone must not expose every private App's instructions.

Namespaced overlay keys separate package identities. Manifest descriptions match on-disk standard skill frontmatter. Descriptions support discovery and bodies load on demand; instruction text cannot widen authority.

Tools and hooks remain package-owned lifecycle registrations. Their execution checks current resource access through ToolContext. Skill visibility is not a replacement for these execution checks.

Binding changes, scope changes, pause/removal, and revocation must refresh the applicable overlay. The native binding enforces the same tenant boundary for every turn.

Test inaccessible Apps, a second workspace, pause, duplicate keys, missing skill files, stale cached profiles, and current-grant rechecks. See [resident architecture](../product/RESIDENT_HARNESS.md).

## Validated package sources

Library source validation is shared within each process, independently of the
short-lived workspace profile cache. Changes to package artifacts, signing
keys, ordered package roots or plugin registrations invalidate source reuse.
Recorded bundle paths must resolve through this validated index; a rejected
package cannot re-enter through metadata or a same-named skill in another bundle.
Caller access is checked afresh before a cached profile can be returned.

Library validation and Skills editor catalogue compilation run outside the API
event loop. Concurrent cold requests and forced rescans serialize compilation;
forced rescans still validate afresh. Catalogue graph updates and structural
edge callbacks remain on the originating request task. Source reuse never
stores caller permission decisions or workspace content.

## Author skills as complete procedures

Declare the exact callable tool names in the standard space-separated
`allowed-tools` field. Include the reads, contract introspection, proposal
operations and readback needed to complete the workflow. Keep each skill focused;
load another skill for an unrelated workflow rather than declaring the whole
catalogue. Explain inputs and outputs, which results are pending approval, and
how to verify the saved result. Prefer returned typed starter payloads to
reconstructing nested objects from examples. Describe absent capabilities and
partial outcomes truthfully instead of promising work after the reply ends.

The installed Harness Skills capability loads instructions; it does not bind
`allowed-tools` itself. Integral consumes this metadata and uses Pydantic AI's
public per-request tool preparation to disclose the declared schemas when the
skill is active, including when activation is restored from framework history.
This applies equally to reads and change proposals, and supports shared tools.
Only tools already present in the current authorized catalogue can be disclosed.
Unrelated tools remain deferred and searchable. Skill text, tool declarations
and restored activation do not grant authority: every dispatch still checks
current scope, permissions, policies and required approval.

After approval or rejection, verify the recorded result and give one or two
short sentences. State what changed or remains unresolved; omit workflow
recaps, repeated proposals and unsolicited offers. Keep exact technical facts
in diagnostics. Do not truncate an uncertain outcome into a success claim.

### Skill context lifetime

A focused saved-text lookup may use the foundational `integral_query` tool
without activating the full Insights procedure. Only its already authorized
read schema is initially visible; no write workflow or additional App tools are
activated. More complex workflows retain normal discovery and loading. The
broker rechecks current scope, policy and declared operation boundaries on every
call, independently of schema visibility.

The authorized workspace skill library is a discovery catalogue, not a list of
procedures to resend forever. Native runtime context policy retains the active
and recent task's procedures, expires older complete load pairs under a byte
allowance, and bounds historical tool disclosures. The agent can reload an
expired skill using the normal authorized discovery/load flow. A skill is never
left marked active after its required instruction body is discarded. Pending
staging/effect continuations protect their task, and final context admission
stops oversized protected work safely. App uninstall, permission checks and
profile invalidation remain Core authorities; context retention grants none.

## Skills settings browsing

The settings page uses `GET /api/agentive/skills/browse` with `page`,
`page_size` (default 10, maximum 50), `search`, `source` and optional `app_id`.
Search matches saved skill names/keys and App names. Counts and App choices
come only from the caller-visible inventory; supplying an inaccessible App ID
cannot reveal its skills or counts. Existing list and effective-context APIs
retain their contracts.

Paging precedes on-disk description enrichment. Only the selected page is
returned, and full instructions load when a skill is opened. The graph registry
still resolves the authorized inventory before paging: this is not a claim of
constant-time database discovery. The initial UI does not compose a runtime
profile or fetch the tool catalogue. The secondary “Inspect agent access” action
opens a dialog on demand, shows one paged collection at a time, and resets on
workspace changes. Tool autocomplete loads
only when creating or editing a skill. Mutation feedback uses toasts.

### Reusable discovery derivations

Capability search and resource metadata ranking reuse content-addressed vectors
in the primary jvspatial store. These are `DefinitionVector` Objects, never
Nodes, resource inventories, model queries or permission caches. The identity
includes the complete local encoder profile (artifact and revision) plus a
source-text digest; source text is not persisted. Changed schemas or encoder
profiles produce different identities. Only currently admitted definitions are
requested by a fresh invocation.

The process LRU retains at most 512 immutable vectors and 200,000 numeric
components. A ranking batch is capped at 2,000,000 components; high-dimensional
cohorts must be narrowed or use lexical ranking, rather than allocating an
unbounded matrix. Cached dimensions must match the current encoder profile. Larger discovery cohorts
read missing derivations in primary-store batches of 250, up to the existing
5,000-resource budget, rather than re-encoding every definition after eviction.
The existing indexing worker removes derivations older than 30 days in batches
of at most 100. Missing or invalid values can be recomputed; cache records never
establish authority. Existing lexical fallback remains explicit when semantic
ranking is unavailable.

Vectors address encoder work, not authority or inventory completeness. They
remain lazy: metadata indexing does not start an encoder per workspace.


## Incremental resource discovery

`ResourceMetadataProjection` Objects store bounded App/Track names, descriptions,
source digests, lifecycle and derived parent hints. They contain no Entry content
or permission decisions. A workspace storage index makes reads selective.
Mutation events refresh the current source after staging/capture checks, even
when audit-event recording is disabled. A source digest change replaces only that
projection; unchanged sources do not rewrite it. Conditional writes prevent a
slow worker from overwriting a newer revision.

The existing indexing worker also repairs missed events and source deletion in
pages of at most 100. A Walker visits rooted workspace App/Track registries; a
durable, conditional progress cursor survives restarts and concurrent workers.
One failed workspace remains pending while peers continue. A malformed scope is
not inferred or promoted to a complete index. No additional daemon is required.

Discovery ranks up to 5,000 metadata rows. Parent hints suppress repeated child
admission once an App has been proved current. Every disclosed App/Track still
passes fresh primary containment and ordinary current-principal policy. A shared
Track does not disclose an inaccessible parent App. Hints may lag changes; the
response marks eventual metadata coverage as incomplete and never treats it as
record evidence or authoritative absence. Formatting preserves the index's
admitted candidate order, including matches in Track purposes and hybrid ranks.
Bootstrap uses canonical discovery, also considering readable Track purposes,
until the repair cycle completes. Execution always rechecks current scope.

A bounded live schema Walker supplies an admitted App's Track directory (up to
25), and details for up to four relevant Tracks, six EntryTypes per parent and
12 field keys per type. Literal declared keys remain intact; omitted details
have explicit incomplete flags. Entry values are never read for this preview.
App descriptions cannot establish that a particular EntryType exists. Generic
record reads reject unknown type keys rather than returning a misleading zero;
a known empty type still returns a valid zero count.

Complete governed-query schemas, keys and catalogue generations take priority
over equivalent workspace-tool aliases within the discovery budget. Descriptors
are capped at 6 KiB per App (4 KiB per standalone Track) and 7,000 bytes per page; whole schemas are omitted rather
than sliced. Shared mode, App and catalogue bindings are encoded once in
`query_invoke_args`; combine these with the selected query key and input params.
The bounded Track directory includes short, currently authorized purposes to
distinguish different collections without inspecting records. Omitted purposes
are explicit. The directory is preserved before optional field previews are
removed, so a detailed schema cannot hide another relevant source. Already-read
schemas supply compact, query-ranked literal field-key hints in the directory;
these contain no Entry values and explicitly mark omissions. Unrelated
source purposes use focused lookups in a native parallel tool round. Native
Pydantic `Tool.defer_loading` keeps the governed-query entry point immediately
available. Activated App skills are not automatically padded with another read
catalogue: native ToolSearch loads the needed capabilities on demand. Native
ToolSearch, Skills, persistence, compaction
and model execution retain ownership of the runtime; these are substrate-data
adapters, not a separate routing/reasoning loop. Current policy, catalogue
revisions, staging, receipts and write recovery remain execution requirements.

Within the active turn, a shared window retains up to 32 small read results
across all read transports, at most 4 KiB each and 16 KiB combined. The latest two
larger results retain their existing bounded previews. Native `ClearToolResults`
uses an 8,192-token trigger; its pair window covers retained facts despite interleaved
protected receipts and already-cleared reads. Completed-turn clearing, write-receipt
protection and the global working-context admission limit remain independent.
This substrate projection is necessary because native clearing does not classify
Integral's mixed read/write tool returns by their per-call effect metadata.

Declared queries preserve a source collection name and its sibling report fields
in `row_collection` and `result_metadata`. A generic report `total` is not an
Entry count. SDK mappings expose a computed `.source_output` view for lossless
adaptation; Core's normalized `rows` are not a one-row success marker. The view
is not a JSON field, so resident/MCP context receives each collection only once.
App consumers retain the query's original paging/coverage contract and current
policy checks. Unknown selections, degraded results and warnings do not imply
that no records exist.

App-bound legacy `find_entries_in_track_type` uses the same literal schema
selection and canonical source windows as the bounded record facade. The type
predicate is applied before Entry hydration; unknown types are explicit errors.
The legacy full-list return shape remains for compatibility, so new consumers
should use paged `read_entry_records` or explicit streamed counts instead.

The legacy App Track-title lookup also pushes its matching neighbour predicate
into jvspatial before hydration. Its final label matcher and current access
checks remain authoritative, including Unicode and duplicate legacy labels.

Preview budgets reserve a complete general filtered/grouped-read input contract
beside the intent-ranked reports. Optional output schemas can be omitted before
an invocable input contract is lost; each omission is explicit. This avoids
forcing callers through repeated sample reads, schema discovery or tool aliases.
SDK validation errors use bounded Pydantic diagnostics without echoing input
values, so callers receive a repairable request error rather than a server fault.
