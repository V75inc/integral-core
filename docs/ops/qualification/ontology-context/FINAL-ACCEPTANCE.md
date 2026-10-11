# Final realistic graph-context acceptance

11 October 2026. Isolated Core `codex/ontology-context` on main base
`9d13b44eb7425b3371f1f2a21f799bdd41dfe02a`; JV Spatial dev Git wheel pin
`7d0853c28e38cfdba068bb9944c7603ab006d3c4`. Native local PostgreSQL,
real JWT authentication, DEBUG=false. Model: DeepSeek V4.1 Flash Cloud through
the authenticated Ollama relay. Local application: http://127.0.0.1:57137/agent.
No original-checkout work, production service, commit or push was changed.

## Scenario and expected answer

This is realistic **fictional** data, not customer or supplier evidence. A
Coastal Cold-chain Pilot App spans Delivery Milestones, Carrier Commitments,
Quality Release and Depot Operations. Twelve records include eight connected
readable records, an unlinked marketing distractor, one explicitly excluded
record, a descendant reachable only through that excluded record, and an
individually readable record in another workspace. Four EntryTypes declare
selected scalar facts and qualified meanings. Ten REFERENCES edges include
seven permitted edges in the launch capture.

| Record | Recorded state | Deadline | Decision-relevant evidence |
| --- | --- | --- | --- |
| Launch | Planned; approval pending | 2 Nov | 12 delivery routes |
| Northstar allocation | Written allocation pending | 24 Oct | Vehicle quantity null; no signed commitment |
| Seabreeze alternative | Proposed; not reserved | 25 Oct | Two refrigerated vehicles; indicative offer dated 9 Oct |
| Seabreeze inspection | Not completed | 26 Oct | No qualification certificate |
| Seabreeze insurance | Renewal unconfirmed | 27 Oct | Old note dated 1 Sep; certificate expires 31 Oct |
| Temperature validation | Route trial pending | 29 Oct | No validation results |
| Logger calibration | Completed | 23 Oct | 4.5 degrees Celsius; calibration certificate recorded |
| Depot readiness | Approved | 20 Oct | Operations sign-off recorded |

The launch requires allocation, route validation and depot readiness. Northstar
allocation has a proposed Seabreeze alternative; that alternative itself requires
inspection and insurance. Route validation requires logger calibration, but
calibration completion does not establish validation completion. The old
marketing record's 100 vehicles are not a current allocation or stored launch
prerequisite. A two-vehicle proposal does not prove capacity for twelve routes.

[Fixture IDs](final-realistic-fixture.json),
[authorized canonical snapshot](final-realistic-projection.json).
The fixture was created through authenticated App/Track/Entry APIs in the
exclusive runtime; type declarations and typed graph wiring were attached to
existing rooted Operational Models. The original simpler smoke fixture remains
available separately. A local same-origin diagnostic uses the signed-in session;
it is not a shipping graph-viewer feature and embeds no credentials.

## Browser results

All **18 structured browser checks passed**, covering real analyst identity,
search and native recorded capture, all eight expected records, exact deadlines,
units/null values, insurance observation/expiry, completed versus pending work,
unlinked marketing exclusion, private/foreign/denied-bridge exclusion, endpoint
closure, completeness and byte bounds, plus denied private and foreign roots.
[Machine receipt](final-realistic-receipt.json),
[browser proof](final-realistic-browser.jpg).
The source-level endpoint/definition closure and revocation tests remain in the
previous full [integration qualification](RICH-CONTEXT.md); this turn changed
only local fixture data and documentation, not runtime source.

Two fresh conversations used ordinary user wording, without tool names, IDs,
discovery instructions, or answer hints:

1. **“Can we launch our coastal delivery pilot on 2 November if Northstar misses
   its truck confirmation, or should we switch to Seabreeze?”**
   Correctly explained the conditional alternative, two unreserved refrigerated
   vehicles, inspection and renewal prerequisites, expiry before launch, pending
   independent temperature validation and recorded depot approval. All seven
   generated record URLs exactly matched captured Entry/Track IDs. It made no
   allocation claim from the 100-vehicle advertisement. Completed in 18.9 seconds,
   four model calls, five tool steps, 69,375 total provider-reported tokens.
   [Answer](final-realistic-query-1.txt), [screenshot](final-realistic-query-1.jpg),
   [trace](final-realistic-query-1-trace.txt).
2. **“Our coastal depot is approved and the temperature logger is calibrated.
   Is the coastal delivery pilot ready to go on 2 November, or what still needs
   to happen?”**
   Correctly said readiness remained unconfirmed, distinguished both completed
   prerequisites from pending route validation and pilot approval, and retained
   the outstanding transport/fallback conditions. All eight table deadlines
   were correct; four generated URLs matched captured IDs. Completed in 17.7
   seconds, five model calls, six tool steps, 85,886 total reported tokens.
   [Answer](final-realistic-query-2.txt), [trace](final-realistic-query-2-trace.txt),
   [persisted answer](final-realistic-query-2-persisted.txt),
   [screenshot](final-realistic-query-2-persisted.jpg),
   [question and answer top](final-realistic-query-2-top.jpg).

These are manually assessed factual/relationship smoke outcomes, not a large
statistical model evaluation. Both responses used recorded ontology. The second
answer was recovered after navigating to its citation and back. Following its
validation link opened the correct Entry and Quality Release track, which showed
pending route trial due 29 October alongside completed calibration due 23 October.
[Citation readback](final-realistic-citation.txt),
[screenshot](final-realistic-citation.jpg). Provider cost is unavailable, not zero.
Reported token totals sum multiple calls and repeated prompts, not unique graph
input. Previously observed generated-link errors remain a model-quality risk;
these two successes do not establish universal hyperlink correctness.

## Measured value and cost

[Search baseline](final-realistic-search.json): `Seabreeze`, graph/keyword channel,
top 20, returned four matching titles, ranked historical marketing first. It
provided no dependency edges, paths, qualified meanings or operational field
values. Recorded context returned the eight connected records and seven stored
edges, excluded marketing, and supplied four referenced relation definitions.
It preserved 129 present and 12 null field states, zero missing/truncated fields.

This directly demonstrates added **relationship evidence and relevance filtering**:

- Separate facts become an explicit prerequisite/fallback chain across Tracks.
- A fallback's own requirements become visible before relying on it.
- Completed supporting work is distinguished from an incomplete parent task.
- Quantities are attributed to the correct commitment, with units and caveats.
- Stale/expiring evidence and unknown values are retained instead of guessed.
- The explanation remains within the user's authorized workspace subgraph.

Search alone took 80 ms; recorded capture took 328 ms and 45,923 bytes under the
49,152-byte request limit. Context adds read work and model input; it does not
prove a latency, token-cost, or accuracy reduction. This is an evidence-output
comparison, not a randomized search-only versus graph-assisted model experiment.
It does not demonstrate faster real business decisions or economic ROI.

## Qualification and release limits

Feature documentation: [Workspace graph context](../../../product/GRAPH_CONTEXT.md).
API/configuration and full source gates: [RICH-CONTEXT.md](RICH-CONTEXT.md).
New fixture/browser/docs evidence supplements the previously passed 5,011 backend,
43 PostgreSQL and 684 CI smoke tests; those suites were not redundantly rerun for
fixture/documentation-only work. Final staged guards, pre-commit hooks, pinned formatting and documentation
link checks passed.

Captures remain best-effort, current-authorized, bounded reads. A complete graph
is not proof that enough information exists to authorize launch. None of the
fictional recorded certifications, capacity or approvals were independently
verified. No remote/production deployment, public compatible JV release, full
production load test, or large model evaluation was performed. The generic Core
contains no coastal-delivery behavior; scenario data and schema declarations
supply its domain meaning.
