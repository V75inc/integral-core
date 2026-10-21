# Declarative indexing and retrieval

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
  "metadata_fields": []
}
```

Defaults select title, body and string custom fields. Non-text values are not
embedded. Metadata fields name explicit `custom_fields.<key>` scalar paths. Choose fields appropriate to the information; never declare secrets.
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

`metadata_fields` stores selected scalar values as derived metadata. It does
not create a new public metadata-filter API. Text selection does not
implicitly include attachment bytes/extracted text, comments or linked records;
those need an explicit supported canonical text field or future adapter.

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
fall back to canonical keyword reads with a bounded retry cooldown. Deletion invalidates
retrieval immediately through canonical reads even if derived cleanup retries.

Use `INDEX_WORKER_INTERVAL_SECONDS` (default 10), `INDEX_WORKER_BATCH_SIZE`
(default 16, maximum 128). Each sweep scans at most 100 Entries per cycle.
Model inference runs off the async event loop; concurrent encoder access is
serialized; `EMBEDDING_MODEL_THREADS` defaults to one ONNX thread per process
(1..16), avoiding CPU oversubscription. This prevents cold model setup and native tokenizer use from
blocking or racing chat requests.

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
Chunks use a clone of the actual model tokenizer without truncation, preserve
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

Hybrid uses reciprocal rank fusion of exact cosine and PostgreSQL lexical
rankings. Explicit graph mode retains the canonical keyword path, including
unindexed content. Scoped exact vector ranking deliberately avoids global ANN
candidate starvation. Profile-specific HNSW indexes are provisioned, but are
not used by this exact query path. Large-scope performance is a separate load
qualification requirement; do not infer it from a small wiki smoke.

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

- `GET /api/tracks/{id}/search-index`: storage AND encoder readiness, jobs,
  bounded comparison of canonical source hashes, delivery receipts and actual
  stored passage hashes. `coverage_complete` is a point-in-time observation,
  never a future guarantee; over 1000 sources/jobs is explicitly truncated.
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

Run the default backend suite and `make verify-ci`. Opt in to real encoder and
pgvector integration tests on a disposable database:

```sh
SEMANTIC_TEST_DSN=postgresql://user@127.0.0.1:5432/disposable \
TESTING=1 INTEGRAL_RUN_SLOW_TESTS=1 backend/.venv/bin/python -m pytest \
  backend/tests/test_semantic_index_definitions.py \
  backend/tests/test_semantic_indexing_pg.py -o addopts='' -q
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

See the [PR #162 qualification report](../ops/qualification/semantic-search-2026-10-20/REPORT.md) for actual accuracy, latency and
provider-reported token observations. A synthetic corpus is not a production
recall or scale guarantee.
