# JV Spatial dev refresh — 11 October 2026

Updated Integral's immutable development source and lockfile from
`7d0853c28e38cfdba068bb9944c7603ab006d3c4` to
`f45f8a066af0ca8dc54f0cfa5b30fb8a9f5b5aeb`. A frozen `uv sync` installed that
exact Git commit, confirmed from installed distribution provenance. No other
resolved dependency or package version changed.

The upstream delta hardens PostgreSQL index ordering/repair under concurrent
workers, single-field index adapter parity, and null/missing `$exists` behavior.
The native rich projection contract is unchanged. Integral needed no service or
skill changes for compatibility.

## Existing-data browser qualification

Restarted the isolated native deployment against its existing PostgreSQL data;
authentication remains enabled. New runtime URLs are
`http://127.0.0.1:61451/agent` and `http://127.0.0.1:61449` (API).

- Dedicated PostgreSQL ontology/database suite: **43 passed, zero skipped**.
- Structured browser checks: **18/18 passed**, including all eight authorized
  records, seven edges, recorded facts/dates/units, complete 45,923-byte capture,
  and rejection of private/foreign roots and denied intermediary paths.
- Fresh ordinary-language question: “Can we launch our coastal delivery pilot
  on 2 November if Northstar misses its truck confirmation, or should we switch
  to Seabreeze?” The resident used `integral_get_graph_context` through normal
  discovery under **DeepSeek V4.1 Flash Cloud**. It preserved unreserved two-vehicle
  capacity, inspection, insurance renewal/expiry, separate route validation,
  completed depot approval, and pending launch approval. It explicitly treated
  the historical 100-vehicle advertisement as unlinked and not current allocation.
  The answer table showed seven decision records; the structured capture also
  retained the completed calibration record.

This is one manually assessed model response, not a statistical accuracy result.
The trace reports five model calls, seven tools, 60.3 seconds and 94,854 tokens;
provider cost is unavailable. The structured capture took 2,904 ms while local
verification was running, versus 425 ms for search. These are single-run local
observations with concurrent load, not comparable benchmarks or a performance
regression conclusion.

Evidence: [browser receipt](dev-update-browser-results.json),
[query and tool trace](dev-update-query-trace.txt), [screenshot](dev-update-query.jpg).
Previous qualification remains historical evidence for its stated commit.
A compatible published JV Spatial release and public wheel qualification remain
required before production shipping.

## Citation-flow privacy correction

Opening a source citation exposed a pre-existing Core feed issue: the paged
entry-list service trusted parent Track access and returned explicitly excluded
Entries. This is independent of the JV Spatial change; the graph-context read
already correctly rejected the private root. The list service now applies the
current user's Entry exclusions, with direct ownership/collaboration taking
precedence, in the database query **before pagination and counts**. Two regression
cases cover track-scoped and global feeds, visible totals, page fill, and direct
grants overriding an exclusion. The workspace/Track gate remains in place.

After the correction, the isolated deployment is available at
`http://127.0.0.1:63443/agent` (`http://127.0.0.1:63441` API). The corrected
browser rerun passed **20/20 checks**, adding feed exclusion and visible-total
checks to the original 18. A Northstar source link opened the expected record
with its pending written allocation and 24 October deadline; the underlying feed
now shows six authorized entries and excludes the private negotiation record.
The graph capture was 141 ms and search 57 ms in this rerun; again these are
single-run observations. Both new feed pagination/count regression cases passed
on PostgreSQL as well as the default JSON test backend, without skips.

Final evidence: [20-check receipt](dev-update-browser-final.json),
[citation readback](dev-update-citation.txt), [citation screenshot](dev-update-citation.jpg).

The first full gate caught query-budget regressions in the initial feed fix
(37 reads versus a 32-read feed limit; 20 versus 15 for graph retrieval). The
implementation was revised to query typed exclusion edges directly and fetch
only matching direct grants when exclusions exist, without hydrating their
records. All five existing query-budget checks passed with their ceilings
unchanged. Invalid legacy collaboration roles do not override an exclusion.

The first edge-only optimization passed direct-node regression tests but failed
the real-login browser feed checks (18/20). JWT principals use AuthUser Object
ids while permission edges originate at User graph-node ids. The final service
resolves the canonical graph user once before scope checks and reuses that id.
A new authenticated API regression covers this distinction; all three feed
regressions and five original query-budget tests passed together. All three feed
regressions also passed on PostgreSQL. The failed diagnostic is preserved in
[the intermediate receipt](dev-update-browser-optimized-failed.json).

Final real-login browser qualification on the canonical-identity implementation
passed **20/20 checks**: [final receipt](dev-update-browser-canonical.json).
The current isolated runtime is `http://127.0.0.1:49492/agent`
(`http://127.0.0.1:49490` API). No frontend source or inference-model setting
changed during the refresh. All development trials use synthetic records.

## Final local gate

`make verify` passed on the final source and installed JV Spatial commit:
all staged substrate guards, pre-commit checks, formatting/lint, frontend types,
artifact qualification, **687 CI smoke tests passed / 7 skipped**,
**1,583 frontend tests passed**, and **5,014 full backend tests passed /
286 skipped** (infrastructure/optional lanes remain explicit). The separate
PostgreSQL probes passed 43 ontology/database tests and three feed ACL tests.
The final browser receipt passed all 20 checks. No query-budget threshold was
increased and no permission cache was introduced.
