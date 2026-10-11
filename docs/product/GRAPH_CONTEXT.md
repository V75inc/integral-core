# Workspace graph context

Development feature on `codex/ontology-context`, integrated with JV Spatial dev.
It is not yet qualified as a published production release.

Integral's resident can answer ordinary questions about dependencies, blockers,
and alternatives using the relationships already stored in the selected
workspace. A user does not need to name a tool or supply an internal record ID.

> “Can we launch our coastal delivery pilot on 2 November if Northstar misses
> its truck confirmation, or should we switch to Seabreeze?”

The insights skill finds the relevant record, calls `integral_get_graph_context`,
and explains the returned evidence. The graph read supplies context; the model
performs the interpretation. JV Spatial does not execute the model.

## What it adds

| Existing read | Added graph context |
| --- | --- |
| Search finds records matching words or meaning | Stored paths show which records relate to the selected subject |
| Separate records describe tasks and alternatives | Qualified meanings distinguish prerequisites from proposed mitigations |
| A record may mention available capacity | Recorded state, quantity, units and verification notes distinguish an indicative offer from a commitment |
| Completed records appear alongside pending records | Paths explain why completing one prerequisite does not complete its parent |
| Search can return an unrelated historical match | A focused capture excludes unlinked matches from the dependency explanation |

In the final fictional acceptance scenario, a search for Seabreeze ranked an old
marketing record first. The launch graph excluded that record and connected the
backup to its inspection and insurance prerequisites. It also preserved the
independent temperature-validation requirement and completed depot approval.
This is a concrete evidence-selection benefit, not a measured population-wide
improvement in model accuracy or business outcomes.

## How to use it

Select the workspace and ask a question about an identifiable record or decision:

- “What could delay this launch?”
- “If this supplier cannot deliver, what does our fallback depend on?”
- “The depot is approved. What else needs to happen?”

The resident searches for the subject and requests a focused recorded ontology
capture. A useful answer names the related records, preserves their dates and
states, distinguishes proposed from confirmed alternatives, and identifies
missing evidence. Review the source records before acting on an inference.

Meaningful relationships must exist as stored edges. Application authors opt
into recorded detail through the Operational Model's `form_schema.graph_context`:
selected scalar fields, labels, units, same-record evidence bindings and qualified
relation descriptions. The feature does not invent relationships from similar
text, automatically ingest all document contents, or certify an operational state.
Authors should choose decision-relevant fields rather than every field.

## Privacy and authority

Every capture stays inside the active workspace under the requesting user's
current resource permissions. Cross-workspace links are excluded even if both
endpoints are individually readable. Denied intermediaries cannot bridge to other
records. Authorization and schema access are checked before exposure; snapshots
are not permission caches. Installed Apps must honor their declared query contracts.

Context selection controls relevance, not field-level confidentiality. Keep
information with different access requirements in separately authorized resources.
The context read is read-only and grants no write authority. It does not reveal
restricted records, counts or work statistics to explain an omission.

## Limits and tradeoffs

The default summary is topology; recorded detail is an explicit richer request.
Captures have depth, record, candidate, deadline and byte limits and expose
coverage/truncation. A complete capture describes represented authorized detail;
it does not mean sufficient evidence exists to approve a decision. Null means
unknown, observation dates may be stale, and verification notes are recorded
claims. Captures are best-effort multi-read evidence, not atomic history.

Graph context adds reads and model input. The final local example took 328 ms
for eight related records versus 80 ms for the search baseline, and produced a
45,923-byte projection. Those are single-run diagnostics on an isolated instance,
not production guarantees. Narrow the root/radius and selected fields; use exact
query/count tools for rankings, totals and inventories rather than inferring them
from a bounded graph. Strong model grounding still matters: local Gemma trials
misattributed dates; final browser acceptance uses DeepSeek V4.1 Flash Cloud as
the user's specified minimum. Model-generated hyperlinks also require review.

Implementation: [workspace contract](../backend/workspace-graph-context.md),
[recorded configuration](../backend/recorded-graph-context.md).
Evidence: [final realistic acceptance](../ops/qualification/ontology-context/FINAL-ACCEPTANCE.md),
[full integration qualification](../ops/qualification/ontology-context/RICH-CONTEXT.md).
