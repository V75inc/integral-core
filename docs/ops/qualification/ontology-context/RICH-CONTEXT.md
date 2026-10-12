# Recorded ontology context qualification

Final realistic scenario and feature value: [FINAL-ACCEPTANCE.md](FINAL-ACCEPTANCE.md).

Qualified locally on 11 October 2026 in `codex/ontology-context`, isolated from
parallel work. Core main base is `9d13b44eb7425b3371f1f2a21f799bdd41dfe02a`.
JV Spatial dev is installed from a Git wheel pinned to
`7d0853c28e38cfdba068bb9944c7603ab006d3c4` in the lockfile and uv source.
No commit, push, PR, production deployment or release version change was made.

Local native deployment: [Integral](http://127.0.0.1:57137/agent), API port 57135.
Its database, credentials, environment and synthetic records are exclusive to
this worktree. The default is now `ollama/deepseek-v4.1-flash:cloud`, relayed by the authenticated local Ollama daemon to cloud inference. The user set DeepSeek V4.1 Flash Cloud as the minimum acceptance model; earlier local Gemma trials are diagnostic failures, not cloud qualification. The Settings provider default already recommends Ollama Cloud / DeepSeek V4.1 Flash; Core has no automatic local-model fallback.

## What changed

`detail=recorded` opts into native JV Spatial projection format v2. Default
summary remains bounded topology. Author-validated EntryType
`form_schema.graph_context` selects scalar facts, labels, units, source notes,
observation dates and verification notes. Qualified REFERENCES definitions match
concrete EntryType, schema revision, track and relation field. Null, missing and
truncated values remain explicit. Same-key conflicts across schemas receive
neutral metadata rather than another schema's evidence or units.

[The configuration contract](../../../backend/recorded-graph-context.md) includes
the authoring example and limits. At most eight represented types, twelve
selected fields per declaration and twelve union custom paths are allowed.
Preflight and rich capture share the candidate budget and cooperative three-second
capture deadline; schema hydration adds at most eight type records in a batch.
Fields are granted per record and schema. Legacy container values are omitted.
A root that cannot fit the byte budget returns a limit error rather than false
permission denial. Relation definitions, paths and endpoints remain closed under
truncation. Host database-work statistics are not exposed.

Workspace scope and current-user authorization apply before traversal and again
before exposure. Foreign endpoints are excluded even when the user can read
both workspaces. Excluded intermediaries cannot bridge to other records. Current
schema access is rechecked; revoked schema grants remove selected fields and
schema selectors. Installed-App contracts remain enforced. These preserve
I-CONTEXT-01, I-ACCESS-01, I-SUBSTRATE-01, I-EXT-01 and rooted graph wiring.
Context selection controls relevance; it is not a field-level privacy mechanism.
Differently governed information belongs in separately authorized resources.

Existing database optimizations remain adopted: bounded principal `find_one`,
batched parent hydration and native bounded batch writes. The earlier
[database probe](RESULTS.md#database-adoption-evidence) is separately attributed;
no new production performance claim is made from this context update.

## Verification

| Gate | Result |
| --- | --- |
| Full backend, four xdist workers | 5,011 passed, 286 skipped, no failures/errors |
| Final context tests | 42 passed |
| Context and database adoption on isolated PostgreSQL | 43 passed, no skips |
| Slow retrieval and tool dispatch | 52 passed |
| Final CI-faithful smoke, no .env, TESTING=1, DEBUG=false, xdist | 684 passed, 7 skipped |
| Frontend source unchanged; earlier suite | 1,583 passed |
| Reproducible wheel and out-of-tree import | Passed |
| Final authenticated browser checks | 27 analyst + 3 outsider passed |

Wheel SHA-256:
`7906a8e3f70534a0c66b3c1cf36944869d4647e49d2edbee7c808d989d990c46`.
This artifact was qualified with prepared dev dependencies, not a public-index
clean-install release lane. [Machine gate results](test-results-rich.json),
[analyst receipt](rich-browser-final-receipt.json),
[outsider receipt](rich-outsider-final-receipt.json) and
[native recorded projection](rich-projection-final.json) preserve evidence.
Final staged substrate guards, pre-commit hooks, pinned formatting and whitespace checks passed after all source and proof updates.

The browser captures four readable records and three qualified relationships.
It excludes the private readiness/source sentinels, foreign record and hidden
bridge. It returns pending validation due 28 October, supplier confirmation due
24 October with unknown capacity, and backup qualification due 22 October with
40 indicative pilot units and no reservation. Source/observation notes remain
recorded evidence. Final single-run browser timings were 4 nodes, 3 edges, 74 ms summary and
114 ms recorded; they are local diagnostics, not load or production guarantees.

## Ordinary-language acceptance

Exact user question: “What could hold up our regional pilot launch on 1 November,
and how would a backup supplier help?” No IDs, tool names or discovery hints were
included. Iterations are preserved rather than counted as repeated successes:

1. A streamed partial response retrieved the rich graph but omitted deadlines;
   it was interrupted during redeployment and is not final acceptance evidence.
2. A failed skill-as-tool invocation produced an unhelpful setup error. The SOP
   was clarified to load the skill and call actual read functions. The generic
   setup-error renderer was not changed in this update.
3. A completed, persisted answer used discovery, search and recorded ontology.
   It explained the prerequisites and proposed mitigation but omitted returned
   deadlines/quantity. The SOP was tightened to require those facts explicitly.
4. A completed answer retrieved the correct snapshot but attributed backup and
   supplier deadlines to the wrong records and substituted verification notes
   for operational state. This failed grounding acceptance. The SOP now explains
   that node `context` stores actual flattened values while `fields` stores
   metadata; it requires a per-record table and same-node attribution.
5. The next persisted answer correctly returned supplier deadline 24 October,
   backup deadline 22 October and 40 unreserved units, but gave validation's
   deadline as 22 rather than 28 October. This also failed grounding acceptance.
   A visible per-record facts table is now mandatory in the skill.
6. A further local Gemma run was interrupted at the user's model correction.
7. DeepSeek V4.1 Flash Cloud completed the exact ordinary question in 10.7
   seconds (TTFT 8.5 seconds), four model calls and five tool steps. The public
   trace confirms the cloud model, two capability loads, two entry searches and
   `integral_get_graph_context` with recorded ontology. The answer correctly
   pairs all four deadlines and states, unknown primary capacity and 40
   indicative unreserved backup units. It keeps reliability validation separate
   from supplier mitigation and attributes the backup note to 9 October.
   The answer survived reload and reopening its workspace conversation.
   [Answer](rich-query-deepseek-persisted.txt),
   [screenshot](rich-query-deepseek-persisted.jpg),
   [tool/model receipt](rich-query-deepseek-trace.txt).

This passes the recorded-facts and relationship explanation smoke checks.
It does not pass complete presentation quality: the model mistyped the track ID
in the validation hyperlink (`...72479...` instead of `...72419...`). The
underlying captured Entry/Track IDs and facts are correct; model-generated URL
integrity is a remaining defect. Provider cost was unavailable, not zero.
The cloud run used 55,166 provider-reported total tokens across calls, not
55,166 unique snapshot tokens.

## Practical limits

Native snapshots are best-effort multi-read captures, not atomic history or
readiness verification. Completeness is representation coverage, not decision
sufficiency. Observation dates and source notes are same-record data; no external
supplier or test result was independently verified. These records are synthetic.
Candidate/output caps do not bound physical database work on every adapter and
byte caps are not token caps. Local model latency and varied instruction-following
remain separate from graph API latency. One successful smoke response does not
establish statistical model reliability. Production concurrency, the full
PostgreSQL suite, remote deployment and a published compatible JV release remain
outside this local qualification.
