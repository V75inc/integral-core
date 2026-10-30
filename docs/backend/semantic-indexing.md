# Declarative indexing and retrieval

The [improvement contract](../platform/knowledge-retrieval-v1.md) and
[ADR-017](adr/017-graph-guided-knowledge-retrieval.md) distinguish the current
hardening delivery from pending migration, integration and scale gates. This
guide describes the implemented behavior; target SLOs remain unqualified.

PostgreSQL deployments use a local text-only ONNX encoder and pgvector. The
registry resolves lazily after `POSTGRES_*` has become
`JVSPATIAL_POSTGRES_DSN`; API import order cannot permanently select a null store.
`EMBEDDING_STORE_DRIVER=null` remains an explicit graph-only deployment choice.
Atlas retains its existing adapter path; passage indexing is PostgreSQL specific.

The [configuration guide](../ops/CONFIGURATION.md#indexing-and-retrieval)
collects deployment defaults; the [rollout runbook](../ops/DEPLOY.md#search-rollout-and-recovery)
covers readiness and recovery. [ADR-016](adr/016-declarative-semantic-indexing.md)
records the storage and ranking decision. Package catalog discovery has its own
[guide](operational-model-search-index.md).

## Selection

Track `search_index` is a complete declaration:

```json
{
  "enabled": true,
  "fields": ["title", "body", "custom_fields.summary"],
  "exclude_fields": ["custom_fields.internal_notes"],
  "entry_type_ids": [],
  "metadata_fields": [],
  "attachments": "content"
}
```

Defaults select title, body and string custom fields, plus schema-declared
numbers, booleans and multi-select choices. Arbitrary nested values and
untyped non-text values are not projected. Schema-declared secret, password,
relation and file fields are withheld, including from explicit metadata;
attachments use their separate selection and authorization path. Explicit
metadata fields name `custom_fields.<key>` scalar paths. Choose fields
appropriate to the information; never declare secrets.
Paths are canonical, not arbitrary object traversal or SQL. EntryType
`search_index` in an operational-model manifest (persisted in `form_schema`)
can narrow the Track selection and disable a type. An omitted declaration uses
the defaults above. An empty `entry_type_ids` includes all types; a nonempty list
restricts it to those canonical type IDs. It cannot re-enable content
excluded at Track level. This works for App-owned and workspace-authored lists.
The OM compiler, materialization, merges and manifest readback preserve it.
For example, a Track-scoped manifest can narrow one type:

```yaml
scope: track
entry_types:
  - key: article
    name: Article
    fields:
      - key: summary
        label: Summary
        type: text
    search_index:
      fields: [title, custom_fields.summary]
```

`metadata_fields` stores selected scalar values as derived metadata and makes
those explicitly selected values searchable as text; exclusions still apply.
It does not create a public metadata-filter API. Comments and linked records are
not implicitly included. `attachments` separately selects `none`, `metadata`
or `content` (default). EntryType declarations can narrow this selection.
Files must belong to the current Entry through `HAS_ATTACHMENT`; a scalar
attachment ID cache cannot authorize indexing or retrieval.

Attachment content consists of native document text and, for images, observations
from a configured multimodal model. There is no OCR step. Generated descriptions
include model-read visible text and descriptive keywords, bound to the file's
content hash. They remain generated observations rather than canonical facts or
instructions. Without a configured vision-capable model, only image file details
are available. Scanned PDF pages do not silently become searchable image text.

File passages return `attachment_id`, `filename`, `content_origin`, extraction
status and page/slide/sheet locations when available. Retrieval rechecks the
current file parent, scan state, source hash, selection and Entry permission.
Parsing and image analysis run in background work, never during search. Use
`attachment_kind` or a literal `attachment_filename` substring to narrow file
evidence; an empty query with a file filter supports discovery without an
embedding request. A bounded result is not proof of uniqueness or complete
workspace coverage.

## Delivery and recovery

Canonical creates/updates retain an index job in the same graph transaction.
There is no model or vector call on that HTTP write. Connector sync also queues
work. Repeated bounded keyset sweeps cover old Entries, seeds, installs, imports,
moves and declaration changes. Each job stores identities/hashes, not copied
content. Vector and graph storage are separate; this is an outbox delivery, not
a distributed transaction.

Workers claim a generation and renewable lease, encode bounded batches, then
check that lease and current canonical source UNDER a cross-worker passage
publication lock. Replacements are atomic. Superseded work cannot overwrite a
newer delivery. Process loss recovers after the five-minute lease. Failures keep
the work pending with exponential retry (up to one hour); status exposes the
exception class, never provider strings or credentials. A malformed source is
isolated to its own retryable job; later records still advance. Invalid profile
settings retain unconfigured intent and preserve primary writes; a corrected
profile is picked up by reconciliation. Storage/model configuration outages
fall back first to the independent indexed lexical projection, then to canonical keyword reads if storage is unavailable. A temporary local admission timeout does not disable other workspaces’ semantic search. Deletion invalidates
retrieval immediately through canonical reads even if derived cleanup retries.

Use `INDEX_WORKER_INTERVAL_SECONDS` (default 10), `INDEX_WORKER_BATCH_SIZE`
(default 16, maximum 128). Each sweep scans at most 100 Entries per cycle.
Local inference uses one dedicated native executor per process. Interactive queries
receive priority between microbatches (default four passages); after eight
queries a waiting background batch receives a slice. Native work retains its
slot until it actually finishes, even if the caller is cancelled. Queue size
and wait times are bounded; this is process-level admission, not a fleet or
tenant quota. Workspace ordering is round-robin only within the selected job
page; long-document checkpointing and stronger fairness remain planned.
`EMBEDDING_MODEL_THREADS` defaults to one ONNX thread (1..16).

Each successful publication retains a count/checksum manifest for the vector
and independent lexical projections. Reconciliation verifies actual rows even
when a source hash matches the last delivery receipt, and reschedules missing
or corrupt data without requiring an edit. Status uses the same manifests and
never invokes a local or paid encoder. Failed cold weight loads are cooled down for 30 seconds instead of being retried for every pending record. For ordinary edits, only unchanged text
from a verified prior Entry/profile publication is reused. Explicit reindex
requests still re-encode all passages. This is not cross-tenant vector caching.

Lexical passages publish before vector inference, so changed text remains
keyword-searchable during a provider outage. Publication remains source/lease
fenced; retrieval checks canonical content and policy regardless of projection.

## Profiles and text limits

A profile identifies model name, deployment-supplied immutable model revision,
dimensions, passage format, chunk size and overlap. A setting change creates a
new space; old vectors never answer queries encoded by a different profile.
The current profile warms through backfill. Old profile data is retained, but
is not a fallback for a different encoder. Do not claim a new profile is fully
ready until source coverage is verified. Configure model/weights in the image;
the bundled MiniLM artifacts are pinned to Hugging Face commit
`d13954661f83248295ba75c1ed411eef3b7b936e` in
`qdrant/all-MiniLM-L6-v2-onnx`. Repository, immutable artifact revision, model
filename, pooling and additional weight files are included in the profile
fingerprint automatically. `EMBEDDING_MODEL_REVISION` remains an additional
operator label, not a separate cryptographic weight-file attestation.

The text-only runtime uses ONNX Runtime, Hugging Face tokenizers and the Hub
client directly. It does not install FastEmbed, Torch or an image encoder;
Pillow 13+ is available for Integral's image features without a resolver conflict.
Set `EMBEDDING_MODEL_CACHE_PATH` for cached artifacts; the historical
`FASTEMBED_CACHE_PATH` still works as a fallback. Docker prefetches the same
pinned files through the production loader.

A nondefault `EMBEDDING_MODEL_NAME` now requires explicit
`EMBEDDING_ONNX_REPOSITORY`, `EMBEDDING_ONNX_REVISION` (40-character commit),
and `EMBEDDING_ONNX_POOLING` (`mean`, `cls` or already `pooled`).
`EMBEDDING_ONNX_FILE` defaults to `model.onnx`; models with external weight
files declare relative paths in JSON `EMBEDDING_ONNX_ADDITIONAL_FILES`.
The first ONNX output must match the selected pooling contract; supported
inputs are `input_ids`, `attention_mask` and optional `token_type_ids`.
No remote Python/model code runs. FastEmbed catalogue aliases are not guessed:
qualify the chosen artifacts, pooling and dimensions before upgrading a custom
model deployment.

Profile format 2 includes this artifact identity. Upgrading creates a separate
space and backfill even though the default MiniLM vectors match the previous
encoder numerically. Canonical records and old vectors are retained; keyword
fallback remains available while current-profile coverage warms. Do not delete
or reuse older vectors solely because their dimensions match.

Defaults: MiniLM L6 v2, 384 dimensions, 160-token chunks and 24-token overlap.
Chunks use an independent clone of the same pinned tokenizer without loading ONNX inference weights. They preserve
canonical text offsets, and include the tail of long content. More than 4096
passages fails visibly instead of publishing a partial document. Inference
refuses input above the actual model limit (MiniLM 256 including special tokens).
A misconfigured dimension/size reports not-ready and leaves jobs retryable.
Queries above 240 tokenizer tokens receive a request error asking for a shorter
search phrase. No external embedding provider is enabled implicitly.

## Authority and retrieval

`POST /api/retrieve` accepts `workspace:`, `app:` and `track:` scopes inside the
validated host workspace. pgvector semantic/hybrid queries narrow candidates
by current readable Tracks plus independently authorized direct Entry grants,
workspace and profile BEFORE applying the candidate limit. Current per-entry
policy, canonical containment, source hash and passage offsets are checked
before returning any title, text, metadata or citation. The existing governed
App query boundary still applies. An index declaration never grants access.
Archived/deleted/moved/revoked/changed sources cannot return stale content.

Hybrid fuses independently limited vector and indexed lexical candidates with
reciprocal rank fusion. Lexical search uses a weighted stored `tsvector` and GIN
index. Small admitted scopes use exact cosine candidate selection. Final ranks
also apply the bounded diversity/evidence policy described under Qualification.
Feed and Track searches default to this hybrid mode, labeled “Hybrid” in
browser settings. Existing browser settings that used the previous semantic
default migrate to Hybrid; older settings did not distinguish that default
from an explicitly chosen semantic mode. A user can select Meaning afterward.
Keyword mode searches the indexed lexical projection without invoking the
embedding model. Legacy API clients may still request the graph mode.
In Feed and Track, Keyword searches run after a short typing pause; Hybrid and
Meaning searches run when the user presses Enter or selects Search.
Larger scopes may use a
workspace/profile-specific HNSW partial index when `RETRIEVE_ANN_ENABLED=1` and pgvector supports iterative
filtered scans (0.8+). The worker builds indexes concurrently; reads do not build
them. ANN is opt-in pending deployment qualification; exact ranking remains the default. A per-table cap bounds physical ANN indexes; scopes without one use exact
ranking. Set `RETRIEVE_EXACT_THRESHOLD=100000` to retain exact ranking up to that
bounded scope size. Low-selectivity recall and production throughput require
qualification; small synthetic tests do not establish fleet capacity.

Explicit graph mode retains canonical keyword reads, including unindexed
content. Degraded semantic/hybrid reads prefer indexed lexical evidence without
calling the encoder. Final validation checks at most four records concurrently,
with the ordinary current policy check for each; a caller-owned PostgreSQL
transaction stays on its owner task. No indexed ACL or process TTL grants access.

Feed and Track views read the ranked selection using optional `entry_ids` on
the ordinary list endpoints (up to 50 unique IDs, intersected before pagination).
The same workspace, App/Track/view filters and canonical access admission apply.
The UI preserves retrieval order, stops automatic paging during a search and
does not replay 429/5xx errors through a compatibility endpoint. Retry is explicit.

Installed-App records require the App's declared query surface; generic Feed and
retrieval endpoints do not open that boundary. Clients retain
`boundary.declared_query_required` and explicit `refused` metadata as a coverage
notice, with a link to the selected App or the Apps list. Excluded rows are not
presented as an empty workspace or a complete negative search. Partial Feed
counts label records **shown** and never display excluded record details or
counts. When an excluded first page has another cursor, a manual **Load more
entries** action allows continued browsing without an automatic request loop.
Search restrictions reset with the query, principal, workspace and search mode;
stale responses cannot restore a previous scope's notice.
An installed App's Track view can return records through a declared query while
generic search has no candidates to attach a refusal to. The Track page also
uses its already-authorized parent App installation metadata for that display
hint. It grants no read access. An excluded empty result replaces the widget's
own empty message, including widgets that do not consume the page's empty state.

`k` defaults to `RETRIEVE_K_DEFAULT` (150, cap 500), `top_n` to
`RETRIEVE_TOP_N_DEFAULT` (20, cap 50). Omitted request values honor deployment
settings. `context_tokens` bounds returned passage text (default 1200,
64..8192), counted using the embedding tokenizer. Titles, citations, envelope
and tool schemas are additional; this is not a promise about total model input
tokens. Results include passages, current title/revision/profile and `action_url`.
Use that source path as returned; never prepend an invented deployment host.
Markdown source links resolve within the current installation and tab. Absolute
current-origin links also use the router; recognized legacy placeholder record
links are repaired, while unrelated external websites retain external behavior.
An optional `index` envelope reports passage tokens, candidate-limit state and
unasserted coverage. No matches does not prove absence while coverage is unknown.

## App passage search

Declared App tools/functions access passages through
`ToolContext.search_entries` (also available on SDK `OperationContext`). The
host supplies principal, workspace and App; tools supply `query`, a stable Track
manifest key or `n.Track.*` ID as `track_type`, an optional manifest `entry_type`,
and optional `eligible_entry_ids`. This does not relax the generic installed-App
query boundary.

Discovery uses a Walker along App → Track, resolving types
through Track → OperationalModel → EntryType (or a directly attached legacy
EntryType). Stable `template_id` identity takes precedence; ad-hoc Tracks use
`relation_type_key`. Stable Track and EntryType keys match their declared values
exactly, preserving distinct keys containing hyphens or underscores. Display
renames do not change selection. Only Tracks without either stable identity
may fall back to normalized title matching. `entry_type` selects a declared
manifest key; omit it for keyless legacy schemas rather than guessing from
a display name.
Ambiguous readable matches return `ambiguous_track`; an incomplete legacy
lookup returns `track_selection_unavailable`. An explicit Track ID avoids
label ambiguity. Existing title-based SDK helpers retain their own legacy
contracts; this new passage API does not adopt title identity for keyed Tracks.

Every graph neighbour page applies identity/type/explicit-ID predicates before
its limit. Discovery resolves at most 500 matching Tracks/types and ranks their
indexed passages without enumerating an Entry-ID prefix. Current canonical
Track/type and per-Entry audience checks still apply after ranking. Matching
schema-scope truncation is reported as partial; unrelated App Tracks/schema types
do not consume that scope budget. Two shared type-selector indexes are built
concurrently by worker maintenance, never by search requests. Derived type
metadata is not public result metadata or a permission grant.

Explicit caller-supplied Entry IDs remain capped at 500. Their canonical parent
Tracks constrain discovery, including in Apps with more than 500 Tracks. `None`
uses governed scope discovery; an explicit `[]` performs no search. Selected-ID
retrieval does not enumerate unrelated user grants. Canonical scope, current
source and permission checks remain mandatory, including after query encoding.

This read-only facade preserves I-GRAPH-01/02 (no graph writes; rooted-edge
traversal), I-SUBSTRATE-01/I-EXT-01 (no domain identity or package imports),
I-RET-01 (current canonical audience/source checks) and I-RET-07 (honest unknown
or partial coverage). No procedural traversal exception is required.

`limit` bounds passage candidates (1..500); returned Entries are additionally
bounded by `min(limit, 50)` and `context_tokens` (64..8192). `graph`, `semantic`
and `hybrid` modes are supported over the current pgvector passage space.
Unavailable semantic inference can fall back to lexical passage search with
`degraded=true`; it does not search unindexed source content.

Results include passages, current title/revision and `action_url`. Coverage is
`unknown` for an unmeasured index, or `partial` when selection, ranking, output
or passage budgets truncate it, or inference degrades. Only a deliberately
empty input/selection reports `full` without inspecting the index. No matches
does not prove absence. Warnings disclose caps and dropped supplied IDs without
listing inaccessible identities. Failures return stable reason codes, never
provider credentials, connection strings or arbitrary exception text.

For delegated agent/connector functions, Core revalidates the exact actor,
function and issuer before selection and result release. The data policy
subject is the current human issuer; it is never a service-kind/human-ID pair.
Revocation prevents release, and a bare service context cannot borrow human
access. Use structured query/aggregation facades for full collections and totals.

## Management

All routes use authenticated identity and the host-validated workspace;
`X-Integral-Scope: ws:<workspace_id>` selects that workspace. It is separate from
the narrower `scope` in a retrieval request. All three management routes require
current Track configuration authority; reading records alone is insufficient.

Example passage request:

```json
{
  "query": "What should a new volunteer do first?",
  "scope": "app:<app-id>",
  "mode": "hybrid",
  "top_n": 3,
  "context_tokens": 600
}
```

Current Track configuration authority in the bound workspace is required:

- `GET /api/tracks/{id}/search-index`: storage readiness, jobs and bounded comparison
  of canonical source hashes, delivery receipts and vector/lexical integrity manifests.
  It performs no inference and does not assert encoder health. `coverage_complete`
  also requires each selected attachment's preparation to be complete and, for
  image content, a current model-generated description. `attachment_coverage`
  reports aggregate pending, partial, failed, limited and unavailable image
  description counts without exposing file contents. Skipped or unsupported
  extraction is reported as limited, even when its filename is searchable.
  Coverage is a point-in-time observation, never a future guarantee; over 1000
  sources/jobs is explicitly truncated.
- `PUT /api/tracks/{id}/search-index`: validate/save a complete declaration and
  retain a rebuild request and revision-bound audit intent in the same transaction.
  Audit delivery happens after commit; stale agent approvals require a fresh review.
- `POST /api/tracks/{id}/search-index/reindex`: queue a rebuild, including
  unchanged sources. Request actor authority is rechecked during page delivery.

The resident uses `integral_get_index_status`, `integral_configure_search_index`
and `integral_reindex_track` through `integral-indexing`. Mutating tools stage
ordinary approval and recheck authority at execution. Proposing/configuring or
queuing is never presented as indexing complete. Insights uses small scoped
passage reads for prose/RAG and cites returned links without rereading whole
records when the evidence is sufficient.

## Qualification

Separate retrieval accuracy from end-to-end agent accuracy. A resident may
rewrite or narrow a query and answer correctly even when a broad workspace
query ranks irrelevant passages first. Include paraphrases, repeated records,
shared boilerplate and short title-only passages in the held-out relevance
set. Compare ranked evidence with canonical source text, not only the final
answer. Matching encoder fingerprints alone does not establish relevance.

PostgreSQL passage retrieval independently samples up to `min(3*k, 500)`
lexical and vector candidates before fusion, returning at most `k` ranked
candidates to current-source/permission validation. Ranking discounts repeated
title wording and exact repeated substantive paragraphs, so one template
does not fill the evidence window. Vector-only title resemblance receives half
the passage weight unless the query's words identify that title; lexical title
matches keep their weight. Substantive evidence can therefore outrank a short,
loosely related heading. Distinct quantities, negation and word
order in body text are not collapsed by approximate word matching. Candidates
are reordered rather than removed by the diversity stage; denied/stale copies
cannot grant access to their readable counterpart. `index.ranking` identifies
this policy as `rrf_diversity_v1`. The score includes the diversity discount
and is not confidence. Existing source offsets, profile identity, token budget
and live admission are unchanged. This needs no encoder change, index rebuild,
new service or model call. The candidate cap still limits recall: diversity
cannot recover a relevant passage absent from the sampled candidate pool.

A title-only hit also receives a bounded prefix from its already-authorized,
hash-checked selected body/custom field when budget remains. This supporting
passage is marked `context_only=true`; it is not represented as a matched
passage. Its snippet and offsets reference current canonical text, it consumes
the same shared token budget, and excluded fields are never added. Prefix
tokenization is bounded to 8,192 characters. This gives the agent evidence
beyond a label without another model or graph request; it does not establish
that an answer outside that prefix was read.

For browsing latency, the Feed uses cursor pages without exact totals. Its
header identifies a partial count as loaded entries. Date/ID-ordered pages
admit a page-sized candidate window plus lookahead and refill past denied
records; exact totals and custom sorts retain their complete-audience paths.
Current containment, workspace and per-record authority remain mandatory.
Feed and Track pages reuse backend type slugs instead of awaiting redundant
type-name requests; legacy payloads retain the lookup fallback. Measure page
selection, enrichment, HTTP time and visible rendering separately from model
routing, first-tool request/start and completed-answer latency.

Run the default backend suite and `make verify-ci`. Opt in to real encoder and
pgvector integration tests on a disposable database:

```sh
SEMANTIC_TEST_DSN=postgresql://user@127.0.0.1:5432/disposable \
TESTING=1 INTEGRAL_RUN_SLOW_TESTS=1 backend/.venv/bin/python -m pytest \
  backend/tests/test_semantic_index_definitions.py \
  backend/tests/test_semantic_indexing_pg.py \
  backend/tests/test_semantic_search_hardening_pg.py -o addopts='' -q
```

For parity with the PostgreSQL CI lane, also set `INTEGRAL_TEST_DB=postgres`
and `INTEGRAL_TEST_POSTGRES_DSN` to a disposable graph cluster and include
`backend/tests/test_pgvector_driver.py` and
`backend/tests/test_pgvector_unavailable_fallback.py` in that explicit run.
Do not point graph contract fixtures at a development or production database.

Each vector test owns/drops unique tables. The canonical graph fixture remains
isolated. Transaction qualification uses `INTEGRAL_TEST_DB=postgres` and
`INTEGRAL_TEST_POSTGRES_DSN`, and
`tests/contract/test_semantic_index_transactions_postgres.py`; that test fixture
creates/resets its own `integral_test` database, so use an isolated cluster.

Use the [qualification guide](../ops/QUALIFICATION.md#retrieval-qualification)
for actual accuracy, latency and provider-token measurement. Retain raw corpora
and run outputs with review artifacts. A synthetic corpus is not a production
recall or scale guarantee.

## Model migration

Workspace model saves retain separate serving/building choices. Rebuild discovery
follows the rooted workspace Track registry with keyset pages and a depth-bounded
frontier; it does not scan every tenant's Entries. Verification compares current
canonical selected text with stored manifests. A committed-intent journal catches
up subsequent Entry changes in bounded pages. A short transactional epoch and
request-row lease fence activation against in-flight commands, configuration
supersession and outstanding delivery. Track-wide index changes restart the
verification sweep; Entry writes do not require a quiet whole-corpus window.
Older partial rebuilds restart under the journal protocol. Old provider continuation
requires explicit consent; current-key/owner/host checks still apply per batch.
Cancel is revision-bound. See [embedding controls](voice-and-embedding-models.md).

These changes do not complete every search phase. Long-document checkpoints,
stronger tenant fairness, provider pooling/query-vector caching, retention, the
remaining lifecycle-trigger audit and production relevance/load qualification
remain in [the delivery contract](../platform/knowledge-retrieval-v1.md).

## Admission and worker recovery

Workspace scope discovery follows the rooted Track registry. When the entire
requested Track scope is readable, ranking does not enumerate the user's Entry
ownership inventory. Partial access uses jvspatial neighbour pages constrained
to the uncovered Tracks before hydration; unrelated workspaces and already
readable Tracks do not enter that grant scan. The declared Entry Track narrows
discovery only: final candidates still receive current canonical containment
and per-Entry policy checks. Entry-only collaborator links use the existing
independent grant-source resolver and the same admission checks; they are not
flattened into direct collaboration. This is candidate selection, never cached authorization. Retrieval stage timings distinguish
scope, audience, profile, encoding, ranking and final validation without logging
query or source text.

Expired retrieval-worker claims compare the unique lease token, not floating-point
timestamp equality. Time determines expiry; generation/status/token fence
reclamation. Graceful workspace-rebuild cancellation releases only its own claim.
Excluded-content intent resolves the canonical workspace even when the legacy
Entry workspace cache is empty. Source and policy checks remain authoritative
while background invalidation catches up.

The resident receives the existing `integral_query` read schema immediately when
its catalogue permits it. A focused fact lookup need not load the complete
Insights workflow/tool schemas. It starts with a small passage budget; live
broker and App operation boundaries still apply. This reduces model roundtrips
and schema input, not the physical model/provider latency. See
[chat latency](ai-chat.md#response-latency-and-activity).
