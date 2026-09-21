# Resident delivery flow

The resident agent delivers a new operational app through one visible sequence:

`discover → clarify → propose → authorize → execute → verify → explain`

`integral_scaffold` owns this sequence. Specialist skills advise or perform
their bounded work but do not restart discovery or request a second approval of
the same accepted design.

| Responsibility | Owner |
| --- | --- |
| New operational app delivery | `integral_scaffold` |
| Existing schema design | `integral_model` |
| Model and package lifecycle | `integral_models` |
| Individual record changes | `integral_entries` |
| Bulk record changes | `integral_organize` |
| Questions and provenance | `integral_insights` |
| Periodic review | `integral_review` |
| App topology and access | `integral_workspace` |
| Dashboards | `integral_dashboards` |
| Cadence | `integral_scheduling` |

## Status language

Progress language must come from a durable state or returned receipt.

| Evidence | Permitted language |
| --- | --- |
| Design artifact recorded | Proposed; awaiting confirmation. |
| Batch open or approval pending | Prepared; awaiting approval. |
| Receipt reports applied or consumed | Applied or created, naming returned objects. |
| Readback confirms acceptance assertions | Verified. |
| Receipt reports failed, rejected, cancelled, or partial | State that result and the repair or retry action. |

“Saved”, “built”, and “verified” are not synonyms. A build is verified only
after readback confirms the promised app, tracks, schemas, views, seed records,
and routines.
