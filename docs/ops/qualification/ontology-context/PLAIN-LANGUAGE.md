# Plain-language insight qualification

Date: 2026-10-11. Acceptance question, without additions:

> What could hold up our regional pilot launch on 1 November, and how would a backup supplier help?

Test context: ordinary logged-in analyst, active launch workspace, Home page,
fresh conversation with no technical prompt history. Trials use installed local
Ollama models only; no paid or shared provider key is used.

## Baseline and iteration

The initial plain-language prompt failed before any workspace read. The exact
acceptance question also failed: the model requested project details/IDs rather
than discovering and reading the available records. [Baseline screenshot](plain-query-baseline.jpg)
and [baseline accessibility transcript](plain-query-baseline.txt) preserve it.

Iteration 1 expanded the insights skill's ordinary-language triggers and added
a dependency workflow: find the subject, capture its relationships, then answer
with evidence and limits. Resident discovery guidance now recognizes current
workspace questions without requiring product/tool names. The exact question
still produced only a promise to search, with no executed read.
[Iteration 1 screenshot](plain-query-iteration-1.jpg) preserves that failure.

Iteration 2 adds a public Pydantic AI capability validation hook to enforce
Integral's existing initial-discovery requirement when a provider cannot force
tool choice. A text-only reply before discovery becomes a bounded model retry;
the host does not classify the user's wording, choose a workflow, execute a
capability, or widen authority. Pending decisions, authorized continuations,
and already disclosed Integral workflows retain their existing paths. Broker
and live workspace/access checks remain authoritative.

A real FunctionModel regression first promises to search, ignores forced-choice
settings, then receives a retry and calls discovery. It runs both with and
without forced-tool-choice support. The adapter/disclosure/search suites passed
58 tests before live retesting.


Iteration 2 reached discovery and correctly ranked integral-insights, but the
small model did not load the skill. [Discovery receipt](plain-query-iteration-2.txt)
shows that returned metadata and the absence of a record read. Iteration 3
required the fitting discovered workflow to be loaded before final prose,
using a bounded output retry budget of three. The small model repeatedly
refused that handoff and exhausted the budget; it was not credited as success.
[Iteration 3 receipt](plain-query-iteration-3.txt) preserves the failed turn.

The handoff retry now names the exact authorized candidate IDs from the
returned discovery metadata so the model need not reconstruct identifiers.
No user wording routes the workflow, and no host-side tool call substitutes
for model execution. The read and write broker retry budgets remain unchanged.
The latest focused adapter/disclosure/search/runtime suite passed 63 tests.
A first qwen3-coder:latest comparison reached the planner but exposed a real
binding bug: dispatch supplies user_id, which plan_query did not accept. The
pure planner now accepts host identity without fetching data; 37 planner and
read-dispatch checks passed. The large model also caused severe local memory
pressure during concurrent checks, so subsequent model trials run without the
large verification suites alongside them.

Iteration 4 (gemma4:latest) loaded the insights workflow but invoked the skill
name as a tool. [Receipt](plain-query-iteration-4.txt) records the failure.
Iteration 5 clarified that skills are instructions, explicitly required schema
discovery, and removed the simple-retrieval stop rule for dependency questions.
The model selected retrieval but called an undisclosed schema; it still failed.
[Receipt](plain-query-iteration-5.txt) preserves that result.

Iteration 6 experimented with correcting undisclosed calls in an after-model
hook. A small synthetic test passed, but a live run exposed invalid durable
history from unresolved rejected tool calls. That experiment and its test were
removed; tool-call pairing and validation stay with Pydantic AI. The surviving
validation hook applies only to text-only output. No experimental response
rewriting or automatic tool execution remains.

The same live trial exposed an independent scope defect in subject discovery:
hybrid retrieval included an independently readable foreign-workspace record.
The model then misrepresented that record and an unrelated readable search hit
as launch dependencies. [Rejected answer](plain-query-iteration-6.txt) is
explicitly failed evidence, not a qualified insight. Retrieval now binds an
omitted scope to the validated request workspace, refuses foreign workspace and
track scopes, and checks current candidate graph membership independently of
vector-store filters before snippets or fusion. Graph, semantic and hybrid
broker regressions use a vector store that ignores scope. The retrieval suite
also now seeds rooted workspace fixtures and checks the current published MCP
catalogue rather than its retired GET route.

Iteration 7 retried qwen3-coder after the planner/scope repairs. It searched,
loaded the skill and called the planner, but the primary subject reads still
had undisclosed schemas. Its generic final answer is rejected.
[Receipt](plain-query-iteration-7.txt) preserves that outcome.

The final disclosure change exposes only integral_query_entries,
integral_query and integral_get_graph_context after a model loads an owning
workflow. Other reads and writes remain deferred. This is schema visibility,
not tool execution or a new permission grant. A native harness FunctionModel
regression verifies no primary schemas before activation, their availability
after loading, unrelated tools remaining deferred, host-bound broker calls,
and a complete durable checkpoint. The dependency procedure is now at the top
of the skill, with an unrelated illustrative example and a rule that search
matches outside captured paths are not established dependencies.

Iteration 8 reached a complete launch-entry capture (four nodes, three edges)
and explained the correct prerequisites, but constructed broken node-ID links.
It is rejected as the final UX. Iteration 9 added plain labels, a 150-word limit
and explicit unconfirmed readiness; a fresh run failed on skill/tool naming.
The host now lists the exact callable names per request and explains that skill
IDs are instructions, not functions. Iteration 10 correctly loaded and called
the reads, but selected a containing track as the root and omitted reliability
validation from its final answer. It is also rejected.

The skill now distinguishes retrieval `entry_id` from container `track_id`,
explains the entry-search result's `id`, and requires correction of an accidental
container-only capture. No fixture IDs, subject-specific routing or automatic
tool execution were added.

Iteration 11 passed the exact question in a fresh conversation on local
`gemma4:latest`. It loaded the skill, queried the subject, then captured the
matching launch entry's outgoing REFERENCES paths at depth three. The complete
native capture contained four nodes and three edges, excluding denied and
foreign records. The final answer named supplier capacity and reliability
validation, explained the backup's capacity mitigation, and said the stored
links do not confirm readiness or guarantee mitigation. It contained no internal
IDs or invented links. [Full receipt](plain-query-iteration-11.txt) and
[answer screenshot](plain-query-iteration-11.jpg) preserve the result.

The capture read took approximately 157 ms; the whole model turn took 188.6 s
and reported 44,591 tokens across four requests. Provider cost was unavailable.
These local measurements are diagnostics, not production latency or cost
claims.

Iteration 12 passed a second fresh conversation with the unchanged SOP and exact
question. It again loaded, searched, and captured the correct launch entry's
four-node, three-edge paths, then explained both prerequisites and the backup's
capacity mitigation with unconfirmed completion/capacity. It selected the
`relationships` view rather than the SOP's `ontology` view; the stored links and
typed metadata were identical, while the declared ontology dictionary was
absent. This passes the plain-language decision answer, not exact argument
compliance. [Receipt](plain-query-iteration-12.txt) and
[screenshot](plain-query-iteration-12.jpg) preserve it. Capture read: approximately
255 ms; complete model turn: 183.5 s, 44,347 reported tokens across four requests;
provider cost unavailable.

Two successive fresh runs passed the decision-answer criteria on the final
skill, including one ontology capture. This is limited local qualification on
one fixture and model, not a broad reliability or production performance claim.
