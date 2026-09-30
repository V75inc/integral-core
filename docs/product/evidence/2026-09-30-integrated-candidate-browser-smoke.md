# Integrated candidate browser smoke — 2026-09-30

## Candidate and deployment

- **Source revision:** `60f6e6fa4c22181ba17bba7b7e2d6001678cafc8` (`codex/local-integration-candidate`), clean at inspection.
- **Compose project:** `integral-core-smoke-60f`, with configuration sourced from the candidate checkout at `/private/tmp/integral-core-integration`.
- **API image:** `sha256:6da410eb64527319d7eb17069fc93ac76c8c3165893ad8a95168c0058e59e008`.
- **Web image:** `sha256:d2366180ce6e490f31a550277486e307f862dc3605e98412958e09dfd909fa73`.
- Both image creation timestamps are after the source commit; their Compose project working-directory labels point to the clean candidate checkout. The API health endpoint reported `healthy` with its database connected.
- **Browser target:** `http://127.0.0.1:19011/`.

## Browser results

Using the isolated candidate's seeded smoke account, I created a blank workspace named `C6 Candidate 60f QA 2026-09-30`. The Tracks page showed zero tracks. I created `C6 First Track Regression`; the new row appeared immediately without a manual refresh and remained visible after a full page reload (one matching track link after reload).

The Apps, Feed, Approvals, Background Tasks, Notifications, Settings, and Agent surfaces loaded. Their expected empty or settings states rendered. The browser console reported zero errors across these checks.

## Evidence boundary

This is supplemental UI evidence for first-track creation and basic navigation on the integrated candidate. It is not a full authentication/signup journey, private-skill display, migrated-App query corpus, deployed HTTP/resident/MCP transport matrix, responsive/accessibility audit, or live-model exam. It does not constitute Product Owner approval or release authorization. The seed account and pre-existing fixture data mean this was not a clean-database release rehearsal.

The earlier candidate qualification remains in [the 2026-09-28 C6 record](2026-09-28-c6-candidate-qualification.md); that record covers a different source SHA.
