# Manage Tracks

The Tracks page opens **Manage Tracks**, using the same library, import, selection-summary and prominent blank-creation conventions as Manage Apps.

- Create your own Track with no parent App.
- Choose a visible Track-scoped operational model from the workspace/platform library.
- Import `.yaml` or `.yml` (also JSON/ZIP through the shared importer), preview its fields/views, publish it to the library, then confirm Track creation.
- App models are rejected by the Track import preview. Existing App-bound creation and Track editing remain available.
- Library publication and Track creation remain separate confirmations; cancelling does not create a Track.

## Import transport repair

Real multipart requests previously failed in jvspatial's generated JSON parameter binding before the handler ran. The import handler now uses the existing Request-only upload pattern and a schema to validate options. JSON-body imports remain supported; query options take precedence. Existing authenticated workspace binding and publication permissions are preserved. Graph creation continues through the existing publication and Track services, preserving rooted graph membership and workspace access boundaries.

## Verification

Automated coverage exercises real HTTP uploads for `.yml`, `.yaml`, `.YML`, publication followed by standalone Track creation, JSON compatibility and invalid preview options. UI coverage exercises blank/model/import flows, rejecting App uploads, workspace filtering, preserving edit/App-scoped creation, and avoiding saving UI helper copy as a description.

In the isolated local preview on port 9155, created **Manage Tracks QA — Blank**, uploaded `track-import-qa.yml`, previewed/published **Reading List QA**, and created a standalone Track from that model. Both Tracks appeared in the workspace list. Imported model selection remains a separate confirmation before creating a Track.

Validation: frontend 349 files / 2,107 tests passed; seven focused dialog cases passed; TypeScript, production build, pinned format/lint, pre-commit framework and all 16 repository guards passed. Targeted ESLint reported zero errors (seven existing effect-pattern warnings). At a 390 × 844 viewport the dialog measured 390px wide and 745px high.

The complete default backend suite passed. All five new HTTP import regressions passed, and the twelve legacy import tests passed when explicitly enabled outside the domain/slow quarantine.
