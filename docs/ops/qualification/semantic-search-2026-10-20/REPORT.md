# Semantic indexing qualification — 20 October 2026

PR #162 follow-up, based on `398d3659c52bd5a2ac89363132d018a7878445d1`.
All measurements used isolated local test data. No production write/deployment.
The primary Core and Business checkouts and the existing local preview were
preserved; implementation used a separate worktree.

## Environment and method

- Native PostgreSQL 14.17 with pgvector 0.8.2, a separate disposable cluster,
  loopback only. Docker's disk was full; no existing Docker data was removed.
  Production/CI PostgreSQL 16 remains a separate qualification environment.
- Actual local FastEmbed ONNX `sentence-transformers/all-MiniLM-L6-v2`, 384
  dimensions, 160-token passages, 24-token overlap, one encoder CPU thread.
- Six fictional repair-café wiki pages, retained in `corpus.json`, indexed by
  automatic reconciliation from an isolated copy of the QA workspace. The
  source/status API verified all six current in the active profile.
- Seven labelled questions: six paraphrases and an exact stocktaking marker.
  Each HTTP mode received the same natural-language queries, App scope,
  `top_n=3`, default `k=150`, `context_tokens=600`. One warm-up plus three
  measured repetitions: 21 warm requests per mode. Requests were spaced to
  respect the endpoint's rate limit; pauses are outside the latency measurement.
- `http-metrics.json`, `candidate-load.json`, `chat-usage.json` and `gold.json`
  retain machine-readable observations. Test IDs are local fixture identities;
  map them to newly created records when reconstructing the corpus elsewhere.

## Accuracy and authorized HTTP latency

| Mode | Correct page at 1 | Correct page at 3 | Supporting passage evidence | p50 | p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Semantic | 5/7 | 6/7 | 6/7 | 215 ms | 347 ms |
| Hybrid | 6/7 | 7/7 | 7/7 | 193 ms | 228 ms |
| Graph/keyword, same natural-language strings | 1/7 | 1/7 | Not a passage response | 46 ms | 60 ms |

Hybrid recovered the exact identifier missed by the small semantic model.
It is the default for this reason. These results do not imply perfect semantic
recall or generalize beyond the small labelled fixture. The keyword comparison
has no model-assisted query rewrite; optimized keyword phrasing can do better.
Graph mode retains its canonical read path and can read unindexed content.

Every semantic/hybrid response stayed within the 600 embedding-token passage
budget. That budget excludes JSON, titles, citations, tool schemas and model
instructions. It is not a claim about total provider input tokens.

## Actual resident chat and token usage

Two identical fresh-chat questions were run through the existing graph-only
preview and the pgvector candidate, using the same QA content and observed
`ollama_chat/deepseek-v4.1-flash:cloud` route. All four answers gave the correct
saved facts and linked a relevant source.

| Observation, two questions | Existing graph-only path | Pgvector passages |
| --- | ---: | ---: |
| Provider-reported input tokens, summed across physical calls | 213,462 | 140,282 |
| Provider-reported output tokens | 1,671 | 1,205 |
| Model calls | 13 | 10 |
| Mean complete chat time | 21.59 s | 21.29 s |

Observed input use fell **34.3%**. Complete chat latency was roughly unchanged;
one question was slower and the other faster. Model/provider timing and routing
still dominate complete turns. This is a two-question observation, not a chat
latency SLA or a billing estimate. Provider token usage was complete; monetary
cost was unavailable. Local ONNX encoding does not consume provider chat tokens.

A separate missing-information chat correctly reported that neither a visitor
phone number nor a warranty period was provided, and cited the distinction
between a repair disclaimer and a warranty. This harder absence check used
seven model calls / 99,179 input tokens / 36.66 s; it is an accuracy observation,
not an efficiency improvement (`missing-information.json`).

## Chrome smoke

An isolated Chrome session signed into the copied QA workspace against the frozen
local build. The agent used `integral_get_index_status` and confirmed all six
pages ready, then used `integral_query` for a paraphrased newcomer question.
It returned the correct facts and a readable source link. Clicking that link
opened the correct Volunteer Onboarding entry with its saved headings/content.
Both chat streams returned HTTP 200 and the conversation continued normally.
There were no console errors. One development startup WebSocket abort warning
was observed; subsequent sockets and requests were healthy. No UI source changed.
See `browser-smoke.json`.

## Candidate-layer load probe

A separate unique pgvector table held 1,000 actually encoded synthetic inventory
passages. Encoding the corpus took 17.02 s and transactional publication 10.49 s.
Across 50 warm queries, query encoding p50/p95 was 48.68/207.47 ms and exact,
scoped hybrid candidate SQL p50/p95 was 36.18/111.49 ms. This probe excludes
current graph authorization, HTTP and the resident model. It ran alongside
regression work and is not a production throughput or large-scope claim.
The exact query path does not use the provisioned profile HNSW indexes.

## Correctness and recovery gates

The real database/model tests exercise:

- HTTP create → durable job → actual encoding → pgvector → authorized passage
  response, including content well past the old 2,000-character truncation;
- current source/revision/citation and passage budget; edits and hard deletion
  invalidate old candidates before asynchronous cleanup;
- tenant/App narrowing before the candidate cap, cross-workspace refusal,
  direct Entry sharing without whole-Track access, exclusion, grant revocation,
  App uninstall and the governed App query boundary shared by REST/actions;
- separate model/revision spaces, atomic guarded publish/delete, superseded
  generation, failed delivery retry and expired lease recovery;
- declarative exclusions, type narrowing, OM roundtrip, reindex coverage,
  configuration permissions, proposal-only nonmutation and approved execution;
- malformed-source isolation and recovery, nonselected/tag-only edits avoiding
  new inference, invalid deployment profile preserving primary writes;
- a separate real PostgreSQL transaction proof that Entry content and index
  intent commit/roll back together.

Final frozen-source validation:

- Default full backend: **7,036 passed, 970 skipped**, 18 existing dependency/
  framework warnings (18m20s). No failures.
- Explicit PostgreSQL graph + actual encoder/pgvector lane: **31 passed**.
- Separate PostgreSQL Entry/configuration transaction and audit-intent rollback:
  **2 passed**.
- Contract/schema/capability-map checks: **27 passed**.
- CI-faithful smoke (`TESTING=1`, `DEBUG=false`, xdist): passed.
- Mypy: **777 source files**, no issues; frontend TypeScript: passed.
- Pinned format/lint and staged substrate hooks: passed.
- Two reproducible Core wheel builds, isolated wheel import, all 17 registered
  portable skills and new retrieval modules present: passed. Wheel SHA256:
  `5778a27d9bfb29654c4ebcb4fed6e9e541c254ae6007d69a9b85b42594643060`.

The PostgreSQL CI job now runs the real semantic tests explicitly, with a local
encoder cache; these tests cannot silently disappear behind the default slow
filter. Slow/model and PostgreSQL tests remain opt-in in the ordinary default
suite; the explicit real-database commands above are additional. External PR
checks and the PostgreSQL 16 runner remain separate from these local results.

## Reproduction

Use an isolated workspace, install the six fictional pages, and update gold
record IDs to the resulting canonical entries. Inspect each Track's
`/api/tracks/{id}/search-index` for real profile coverage, then run:

```sh
INTEGRAL_BENCHMARK_TOKEN='<local test token>' backend/.venv/bin/python \
  backend/scripts/qualify_semantic_retrieval.py \
  --base-url http://127.0.0.1:19043 --scope app:<test-app-id> \
  --gold docs/ops/qualification/semantic-search-2026-10-20/gold.json \
  --output /tmp/semantic-results.json --repetitions 3
```

Provide the token through the environment; do not commit it. The benchmark
refuses redirects so bearer credentials cannot be forwarded to another origin.
See `docs/backend/semantic-indexing.md` for configuration, lifecycle and the
explicit real-pgvector/transaction test commands.


## Documentation and local citation follow-up

The full backend, PostgreSQL and wheel evidence above describes the semantic
implementation committed as `8d5cfef8`. The following documentation/frontend
follow-up does not rerun or reattribute those measurements to a different wheel.

Updated both tracked AGENTS.md files, invariants, architecture, deployment and
configuration guides, user guidance, changelog and generated route inventory.
Consolidated the release qualification entry point and preserved rc20 evidence
under an explicitly historical path. The documentation reader builds 109 pages;
all 122 maintained Markdown/README/AGENTS files pass local link/anchor checks,
and the active-doc check passes 764 relative targets.

Chrome reproduced a saved chat citation pointing to `https://integral.local` and
opening a new tab. The shared Markdown link component now routes known typed
placeholder citations and current-origin links within the active installation;
the insights skill preserves returned relative source paths. Unrelated external
sites retain external-link semantics. Negative regressions cover lookalike
hosts, other installations, credentials, ports and protocol-relative paths.

The same existing reply was reloaded at `http://127.0.0.1:19009`. Both citation
anchors became relative paths without a new-tab target. Clicking Tool Safety
opened the saved Tool Safety record in the same tab at that local origin; no
additional tab or console error appeared. This frontend smoke used the existing
QA API on port 19040, not the isolated semantic benchmark API. The local frontend
on 19009 now serves this follow-up source. Four shared/streamed Markdown test
files pass (17 tests), and TypeScript passes. These are additional frontend checks;
production smoke and remote CI for the final commit remain separate gates.
