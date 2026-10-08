# Resource presentation and model route receipt follow-up

## Shared UI changes

Conversation rows use one compact title line across the available width, with the timestamp underneath and the options control at the bottom right. Full stored titles and available creation date, latest activity date, message count, and live working state appear in a viewport-bounded preview on pointer hover or keyboard focus, outside the scrolling rail. Escape, blur, and scroll dismiss the preview; opening another preview dismisses the previous one. Search and Rename remain available.

Entry detail fields own a consistent label above each control. Standalone relation editors retain their existing labels; grid-hosted editors suppress that duplicate label and inset. Typed `file` / `files` fields resolve through the authenticated attachment endpoint and show a named preview control. Loading, unavailable, and blocked references do not display their stored IDs. This is recorded as I-FIELD-RESOURCE-01 in `docs/INVARIANTS.md`; ordinary text and fingerprints are not treated as references.

Local browser evidence covers the crowded Agent sidebar, keyboard reveal and Escape, search filtering and Rename menu availability, and the document detail with chat open. Clicking the named rendered-file control opened the existing authenticated one-page PDF viewer. No record changes or model requests were made for this UI check.

## Generic route receipt attribution

Physical model observations now retain the resolved route's credential source and opaque credential reference in the encrypted observation payload. Secret keys are not included. Old observations remain readable with unknown attribution; they cannot serve as exact route proof.

The internal bound-observation reader requires an expected resolved route and validates both persisted intent and terminal outcome against exact execution scope, request identity, provider/model, credential source/reference, attempt, dispatch time, transition identity, terminal status, and aware non-retrograde observation time. Its caller remains responsible for authorizing the owning run. Late outcome persistence also checks credential attribution against the original intent.

Focused native harness checks passed (65 tests). The combined disposable PostgreSQL and native selection passed (227 tests), including three actual encrypted-store receipt cases for platform, workspace BYOK, and local routes. Those store fixtures are synthetic and make no provider request. They verify concurrent duplicate persistence, encrypted storage, reload from a new GraphContext, wrong-credential refusal, and immutable-transition conflicts.

This source groundwork does not settle provider prices, confer dispatch authority, produce executable mandates, or establish a billing invoice ledger. The deployed API during these UI checks remains Core `80d33425`; this newer backend source has not been deployed by the UI update. Cost-unavailable evidence remains unavailable.

## Evidence locations

Local screenshots are retained outside the repository under `venture-qualification-recovery-evidence/evidence/`:

- `conversation-labels-final.jpg`
- `entry-field-labels-and-file-link.jpg`
- `entry-file-reference-preview.jpg`

Full repository gate results are recorded after the final source has stabilized. No release publication or merge is included.
