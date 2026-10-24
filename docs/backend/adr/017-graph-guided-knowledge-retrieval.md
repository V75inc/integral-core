# ADR-017: Strengthen embedding-based hybrid semantic search

**Status:** Direction accepted; initial implementation in review. Safe profile cutover and production resource/scale qualification remain pending.
**Date:** 24 October 2026. **Draft revision:** 4.
**Deciders:** Integral product architect and engineering owners of retrieval, runtime, authorization, SDK and settings.

## Context

Integral needs relevant, relatively low-latency semantic searches over growing
multi-tenant knowledge, with few services to operate. It already supplies a pinned
CPU ONNX default, workspace-owned provider overrides, PostgreSQL passage vectors,
lexical/graph compatibility paths and durable indexing work. JV Spatial supplies
the authoritative graph and bounded relationship projections.

The inspected implementation deliberately ranks the narrowed passage scope exactly;
its provisioned HNSW index does not accelerate that path. Lexical text is computed
at query time, PR #172 App discovery caps Entries before relevance ranking, and bounded
status/receipts cannot establish complete index integrity. Local query and indexing
inference also share a process lock. Cloud paths still load local inference weights
for tokenization. These are baseline observations, not measured production bottlenecks. The linked contract distinguishes the initial PR #171-based hardening delivery from the remaining work and the separate PR #172 SDK findings.

The default local model runs on the hosted server. CPU, native inference memory,
queue contention, replica/process duplication and noisy tenants are operating
concerns. Replacing it with a cloud provider moves some inference cost and adds
network/credential/egress constraints; it does not remove database indexing cost.

## Decision

Adopt the [semantic search improvement plan and contract](../../platform/knowledge-retrieval-v1.md).
Retain embeddings for prose discovery and improve hybrid retrieval with independent
indexed lexical search, exact/structured operations and selective JV Spatial graph
enrichment. Keep PostgreSQL/pgvector, the existing durable work substrate and one
bounded encoding interface as the initial architecture. No dedicated vector
service, GPU, generative reranker or second canonical knowledge store is required.

Keep the current pinned local default initially and preserve cloud overrides.
Qualify model relevance, resource use and provider capabilities. Separate chat
models from embedding profiles, composition from inference loading, primary writes
from encoding, and interactive admission from background throughput. Use fair
bounded scheduling and explicit resource/deadline limits.

Repair lifecycle/integrity first, then indexed lexical retrieval and efficient
passage reuse, scoped exact/ANN planning and App admission at scale. Build pending
profiles alongside an authorized serving generation and activate only after
catch-up/verification. Preserve consent, current source/permission checks, direct
Entry grants, App operation boundaries and honest partial/degraded results.

Manage physical indexes, retained spaces and caches explicitly; workspace-specific
profiles must not imply an unbounded number of HNSW indexes. Publish a measured
hosted capacity envelope, including indexing contention and cloud provider latency.
JV Spatial owns generic canonical query/projection improvements; Integral owns
admission, search adapters, representation and evidence contracts.

## Options considered

| Option | Complexity | Strength | Limitation |
|---|---|---|---|
| Improved embeddings + lexical/structured reads + selective graph | Moderate incremental work | Broad prose discovery with exact facts and existing infrastructure | Requires controlled encoding, migration, relevance and ANN/filter qualification. |
| Embedding-free lexical/ontology retrieval | Moderate | Removes encoder/provider dependency from corpus indexing | Can miss unmodeled paraphrases; graph declarations do not cover all prose meanings. Keep as measured alternative/fallback. |
| Embeddings alone | Initially small | Useful semantic candidate discovery | Weak exact identifiers/totals and incomplete evidence semantics; still needs freshness/authority controls. |
| Dedicated search/vector engine immediately | Higher operating burden | May offer future capacity or ranking features | No demonstrated necessity; does not repair current source, lifecycle or permission gaps by itself. |
| Generated linked knowledge files as authority | Significant governance/migration work | Readable interchange and authoring | Duplicates graph identity/access/lifecycle; useful optional export does not justify a second authority. |

## Tradeoffs and consequences

Embedding models can remain stable independently of chat model updates. Necessary
encoder/composition migrations are explicit, resumable and isolated. Model quality
and supported languages remain empirical obligations, not properties guaranteed by
using vectors. ANN trades exact nearest-neighbor recall for speed and must be
qualified under tenant/selectivity constraints.

Local inference avoids mandatory remote processing but consumes hosted resources.
Cloud choices add latency and provider costs. The plan controls both through
admission, fair scheduling, bounded work, usage accounting and qualified deployment
sizes. Optional encoder isolation is justified only by measurements and does not
authorize broader runtime replication beyond existing transport/turn-state gates.

Independent lexical availability improves outage behavior but is not presented as
equivalent semantic quality. Selective graph evidence improves relationships and
provenance without promising general inference. OKF-inspired representation and
interchange remain optional follow-up work.

This revision supersedes the earlier unimplemented embedding-free proposal. It
does not retire current embeddings or weaken ADR-016 and I-RET-01..07. Detailed
invariant mapping, phased implementation, migration, qualification targets and 24
acceptance scenarios live in the linked plan to avoid duplicate task lists.

## Action items

1. Establish exact baseline, held-out relevance tasks and hosted resource tests.
2. Repair integrity, durable lifecycle and truthful generation status.
3. Control hosted inference/provider execution and separate tokenizer loading.
4. Deliver independent lexical search, passage reuse and scoped ANN/admission.
5. Deliver verified profile activation/retention and simple settings UX.
6. Qualify native/SDK/MCP parity, artifacts, rollout, rollback and capacity.

The proposal is ready for architecture review. Acceptance, implementation,
publication, merge and production promotion are separate gates.
