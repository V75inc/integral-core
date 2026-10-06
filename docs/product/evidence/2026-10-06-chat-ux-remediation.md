# Chat UX remediation execution evidence

## Environment

- Branch: `codex/chat-ux-remediation`; the PR commit is the source revision for this report.
- Local Docker UI: `http://localhost:9006`; API: `http://localhost:4000`.
- Resident binding: `integral_native` (Integral AI), using the public Pydantic AI integration.
- Live provider/model: Ollama `deepseek-v4.1-flash:cloud`.
- Synthetic principal: Integral Smoke Tester; synthetic Home Maintenance, Book Lending, and House Plants Apps.
- Evidence contains no credentials, real customer records, or private model reasoning.

## Implementation and browser receipts

| Package | Implementation | Observed qualification |
| --- | --- | --- |
| UX-01 | Closing invalidates pending deep-link loads and removes entry query context. | Chat and dashboard links opened the correct record. Close and Escape remained on Loans; Back closed and Forward reopened Dune. |
| UX-02 | Scoped semantic decisions, precise proposal authority, batch tools exposed through the entries skill, scoped heading deduplication. | Verbal rejection and button rejection left Loans empty. Three requested loans were then applied through one itemized batch review. Conversation Inbox approval updated Dune once; reload showed Returned = Yes. |
| UX-03 | Compact public activity and tool receipts, collapsed by default; user expansion retained through turn completion. | Keyboard expansion exposed ordered capability/model tools. Private model reasoning is explicitly omitted. |
| UX-04 | Optically aligned Integral mark, stronger opacity/scale pulse and stable label fade, reduced-motion override. | Working and completed labels remained legible in dock and full chat. User reviewed the optical adjustment. |
| UX-05 | Assistant UI owns viewport following; competing custom scroll mechanism removed. | During a docked schema turn, manual scroll-up remained at scrollTop 0 as content grew; Scroll to bottom restored the bottom. Mobile chat had equal page clientWidth and scrollWidth. |
| UX-06 | Durable upload identity, scoped attachment preflight/execution, profile read/diff/publish recovery, canonical publication review fingerprint. | Synthetic receipt appeared on the porch-light record. Condition published with Good/Worn/Damaged choices visible in the entry editor. Optional Notes publication showed the server diff and three inspected records, with zero validation failures/migrations. |
| UX-07 | Physical request usage retained and deduplicated by request identity; unknown cost distinguished from zero. | Persisted request ledgers matched message observations for six-call setup (157,913 tokens), six-call record proposal (86,214), and two-call rejection (35,783). UI rounding matched. Provider cost was unavailable and displayed as such. |
| UX-08 | Canonical dashboard validation before staging, authorized rendered-data readback, real entry links. | Home Maintenance dashboard survived reload and record edits. Book Lending rendered 2 out / 1 returned / 3 total, borrower/date charts and clickable records. After Dune's update and reload it showed 1 out / 2 returned / 3 total. |

## Root causes corrected during qualification

1. Docker copied an ignored, stale `backend/app/resident_harness` tree, which overrode current skills. The image now copies canonical `agent/` assets explicitly and excludes the generated tree from its build context.
2. The entries skill described batching without disclosing its batch tools. Its allowed tools and workflow now include begin/commit/cancel. A fresh live request used one batch instead of repeatedly colliding with the pending-proposal limit.
3. Profile publication previously offered a generic summary. Core now computes the actual diff and binds consent to both reviewed schema snapshots. Changed snapshots and unreviewed legacy proposals fail before publication.
4. An interrupted response could persist a blank assistant turn. Cancellation now persists a useful interruption notice, even before any model content. A reload after a confirmed loan write showed the notice and the durable write independently; no write was replayed.
5. A fresh House Plants build supplied a singleton sort object, then a blanket one-attempt guard prevented correction despite a cancelled staging-only batch. The view adapter now preserves that exact sort as a one-item list before canonical validation. Trusted pre-effect validation errors use Pydantic AI's `ModelRetry` with a two-retry tool budget; partial, applied, authority, and unknown outcomes cannot replay. A real FunctionModel test exhausts that budget at three total attempts. Saved design continuations are disclosed immediately without unrelated catalog rediscovery. The saved House Plants design recovered in 16.4 seconds, with one original design approval and no second approval.

## Final ordinary-language smoke round

House Plants was built from a plain request, populated with three plants through one batch approval, and updated using verbal “Yes”. Aloe’s last-watered date changed to October 6; the table reordered immediately and the assistant correctly identified Fern as least recently watered. The persisted structured date was verified independently of the historical body text.

“Please file this receipt” with a synthetic text receipt produced one destination clarification and no write. After “Attach it to Aloe in House Plants instead”, one approval linked the existing upload. Reloading Aloe showed exactly one attachment with the expected filename. No receipt amount was forced into the plant schema.

## Retained screenshots

- [Dashboard after the record update and reload](chat-ux-remediation/integral-book-dashboard-after-update.jpg)
- [Narrow chat with the durable interruption notice](chat-ux-remediation/integral-mobile-recovery.jpg)
- [Receipt attached to the intended record](chat-ux-remediation/integral-filed-receipt-entry.jpg)
- [Fresh House Plants default table after population and reload](chat-ux-remediation/integral-house-plants-verified.jpg)

- [Clarified receipt on Aloe after reload](chat-ux-remediation/integral-clarified-receipt-filed.jpg)

## Limits of these receipts

- Provider cost was not supplied for these Ollama calls; this verifies honest absence handling, not a priced invoice reconciliation.
- The model used “overdue” language without a configured watering cadence; only the date ordering was established, not a watering recommendation.
- Large token totals are accumulated physical request usage. This work does not establish a production latency or spending target.
- The model described a Still out saved view that was not visible in the Loans UI after an interrupted original scaffold. Dashboard counts and entry links were independently verified; the narrative assertion is not treated as evidence of that view.
- Full-page and conversation Inbox are separate presentation surfaces. The conversation Inbox was tested for the staged proposal; this report does not claim every global policy notification path was exercised.
- Tenant denial, replay races, stale publication, and reduced-motion behavior have deterministic test coverage; this is not a browser audit of every principal/provider/route permutation.
- A GPT control could not be configured through this synthetic user's model settings without adding a key (Save remained disabled). No new credential was supplied. This run establishes live DeepSeek behavior, not a fresh GPT browser result.

## Release gate

`make verify` passed on the final source tree (exit 0), including all staged guards, hooks, pinned formatting, types, clean wheel, CI-faithful backend smoke, 1,323 frontend tests, and the full backend suite. `make test-postgres-ci` passed against isolated test databases. PR CI is checked separately after pushing. An earlier verification started before the publication formatter existed and its annotation-import test saw the old loaded module; that run was not accepted as a release pass. The final run uses the completed source tree.
