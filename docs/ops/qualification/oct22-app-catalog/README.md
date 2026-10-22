# Single App catalog qualification — October 22

Manage Apps is the canonical discovery, inspection, import, and installation view for App packages. Settings links to `/apps?manage=1`. Legacy `/models/:id` links for App packages redirect to that same manager with the requested package open. `/models` retains reusable Track model tools, excludes App packages, and links to Browse Apps.

The manager uses the same operational-model library API and existing App-scope/workspace visibility filters. No backend or installation contract changes. Host install admission, required settings, owner uninstall checks, sample-data options, and the explicit installation confirmation remain in force.

## Detail content

The read-only detail pane retains the full profile description and any different package description; version, scope, and package identifier; optional sample record count; each prescribed Track and its description; expandable record types with field names, keys, and types; taxonomy groups/tags; views and default indicators; declared skills, agents, tools, and hook counts. Missing sections are shown as empty rather than invented. Add/remove selection is available from the detail view. Returning to the library preserves the search and pending selection.

## Browser evidence

Verified against the disposable October QA PostgreSQL runtime on API 4469 and Vite 9149 using the synthetic QA Owner account and six QA package templates. The personal installation is not used.

- `/apps?manage=1` opens the manager directly and lists all six workspace templates.
- Selecting QA Projects then viewing its details retains the selection alongside the description, Records Track, record type, and default Feed view.
- At 390 × 844, detail content scrolls in the dialog and the footer retains creation/review actions. Mobile review retains QA Projects. The viewport override was reset afterward.
- Import package opens the existing reviewed import workflow above the manager; Cancel returns without publishing.
- `/models` displays the reusable Agent Scratch Track model and no QA App templates. Browse Apps returns to the canonical manager.
- No new package was installed or deleted during this qualification.

[Desktop detail view](details-desktop.jpg) · [Mobile detail view](details-mobile.jpg)

## Automated checks

Regression coverage includes full description/Track/field/tag/view inspection, retained selection, direct package opening, close/reopen behavior, old App detail redirect, exclusion of Apps from Track model tools, and Settings linking to the manager. Existing installation, setup, uninstall, workspace visibility, and error-handling tests remain in the frontend suite.

Final validation passed: frontend suite (346 files, 2,046 tests), production build, CI-faithful backend smoke lane (`make verify-ci`), and `make guards precommit format-check lint types`. ESLint reported zero errors and the existing warning backlog. The navigation regression caught a missing Text import during development; it was fixed before the successful final suite and build.
