# Core rc20 release qualification

Candidate: `0.1.1rc20`, based on rewritten main `1ee8929cc1e1e084279ca99ad73ac474d3ad9e26` (PR #145 integrated). Qualification is local; publication and PR CI are separate gates.

## Source and artifact

The candidate updates the package version, frozen lock, CLI fallback, Docker assertion and image-workflow assertion together. The owning App facade can maintain computed readonly relations; direct edits remain readonly, changed targets still require current read authority, and wrong-App writes remain refused. This preserves the Core/App boundary, canonical mutation validation and scoped authorization contracts.

The clean wheel includes the generic frontend and resident skills. SHA-256: `95c3e569db26c8481fd0f47f1f696c29de48088eb560f9e0bc1bdf821cd8b9d1`. All 754 packaged Python source files match this checkout byte for byte. The legacy source-only `app/packages/__init__.py` shim is excluded by the existing build configuration.

Business installed that wheel using its actual installer into a new Python 3.12 environment. Runtime dependencies came from PyPI; `pip check`, Core/JV Spatial version assertions and PDF rendering passed. Core imports from site-packages, with no application-source overlay or edited installed code. Business owns the host modules and domain UI.

## Executed checks

| Gate | Result |
|---|---|
| Full `make verify` | Passed: 6,790 backend tests, 939 environment/explicit-gate skips; guards, format/lint, types, CI-faithful smoke and wheel import passed |
| Frontend suite | 1,864 passed in 326 files |
| Relation authority on PostgreSQL | 22 passed, including owning facade, direct-edit denial, unreadable target denial and wrong-App denial |
| Core Markdown paths and anchors | 105 files, zero issues; active-doc checker also passed |
| Business unit files on the installed wheel | 512 passed, nine PostgreSQL skips across 45 files |
| Business billing on PostgreSQL | Nine passed, including independent processes, workspace retry isolation and legacy receipts |
| Business package workflows on PostgreSQL | 48 passed |
| Business frontend overlay | Build passed; 1,907 tests passed in 333 files |
| App inventory | All 26 manifests and declared handler imports passed; App naming passed |

## Fresh-install browser and readback

A dedicated workspace and synthetic owner/member accounts use a new native PostgreSQL installation, private persistent keys and isolated attachment storage. The browser is served at `http://127.0.0.1:9150`; the API is on port 4450.

- Browser signup/login, workspace selection and App installation/setup completed. Nine Apps are active: Organization, Document Templates, Documents, E-Sign, Email Log, Finance, HR, Recruitment and Payroll Guyana.
- Finance's package view created a customer and an ad-hoc draft invoice. Scoped readback confirmed quantity 1, price GYD 125.50, VAT GYD 17.57, total GYD 143.07, the typed customer link and draft status. The bookkeeper role was assigned using the owner action-binding API. An unauthorized attempt was refused before assignment; failed receipts were reconciled before retry.
- An ordinary member opened a seeded synthetic contract PDF, drew a test signature in the visible browser and submitted it. The wizard advanced to step 3. HTTP readback confirmed the accepted state, signed attachment and HR contract record. Repeated acceptance reused that record; regeneration after acceptance was refused.
- Another employee's form, signing-metadata tampering, protected-file upload and anonymous public contract routes were refused. The ordinary member could not read the private invoice or invoke its creation operation.
- Actual Ollama responses returned the requested readiness text before and after API restart. Credentials were configured through the supported API without recording keys in evidence.
- Restart readback preserved all nine Apps, exact invoice/line values, accepted contract and PDF bytes. A clean Agent reload had no console warnings/errors.

## Documentation cleanup

Retired 57 dated Markdown paths plus obsolete timeline-only material. Updated links and regenerated the source API inventory (472 routes). Outstanding acceptance work remains in [hardening follow-up](../../product/HARDENING_FOLLOW_UP.md). Business separately retired eight dated Markdown files and retained maintained installation, checkout-policy and qualification guides. Removed documents remain in Git history and a verified private recovery archive.

## Limits and recovery

This qualifies the tested local wheel/host configuration. It does not establish TestPyPI publication, Docker images, Atlas, production services, every provider/connector, native-platform installers, or migration of an existing workspace's saved App definitions. The contract PDF was a synthetic fixture; browser generation from a real employment template is not claimed. No external payments or delivered email were used.

Docker's internal disk had insufficient space for the fresh database container, so the supported native PostgreSQL helper was used. Existing containers, development databases and dirty checkouts were preserved. API signup/App-install HTTP timeouts were reconciled against persisted outcomes before resuming. Browser bursts hit rate limiting during rapid modal reloads; normal settled readback and the final clean reload passed. Unconfigured plugin signature verification and no-op attachment screening in this local setup do not qualify production trust/screening.

Private raw evidence is under `/Users/eldonmarks/.codex/recovery/rc20-qualification` and the isolated runtime under `/private/tmp/rc20-business-runtime`. These paths contain private fixtures/configuration and are not committed. Retain the current keys/database/files for further review. Do not merge pre-rewrite Core ancestry into the new main.

## Governed AI edit readback

The actual Ollama chat staged a synthetic Finance customer phone update for explicit review. Scoped readback confirmed the field remained empty before approval. The browser review showed the exact `phone = 555-0120` change; after approval, scoped record readback confirmed that value and the pending approval cleared. No unreviewed mutation was applied.

## Chat title popup follow-up

The initial fresh-install wheel digest above remains the historical qualification identity. The subsequent frontend-only fix expands legacy 60-character opening-message labels using a current authorized thread read, without renaming stored conversations. The popup wraps complete text, supports scrolling within the viewport, and removes the duplicate native tooltip. Async results are discarded when the card closes or changes. Unicode titles are handled by code point rather than UTF-16 code unit.

The follow-up frontend suites passed: Core 1,866 tests; Business overlay 1,909 tests. Eight focused popup tests passed on the final source in both builds, including Unicode labels, mismatched opening text, dismissal and scrolling. Both production builds/type checks passed; lint had zero errors (existing warnings remain). Browser readback on port 9018 showed an existing long title in full inside a 560-pixel card, with no vertical clipping.

The rc20 wheel was rebuilt with this UI, installed as an update in the qualification environment, and passed `pip check`. Follow-up wheel SHA-256: `9c66f696c818df1d67e02af0e7b8ef9a42b88d05f984f6a2d1fdd0a0a156021c`. All 754 packaged Python files still match source; backend code is unchanged from the full backend/PostgreSQL qualification above.
