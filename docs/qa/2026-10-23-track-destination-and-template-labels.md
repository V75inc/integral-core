# Track destinations and plain-language template labels

## Current App-context contract

When opened from an App, Manage Tracks is locked to that App. Its destination
is read-only; top-level placement and Change App are unavailable. Track creation
uses the originating App ID, including after navigating Back from Track details.
The main Tracks page retains both destination choices and App search.

The current change passed 17 Track dialog regressions and TypeScript checks.
Browser review confirmed the originating App name with no destination-switching
controls. The earlier App-to-top-level behavior recorded below is superseded;
its observations are retained as historical evidence.

## Earlier qualification

Manage Tracks now starts with **Add track to**. Users may choose **Top-level Tracks** or an existing App in the active workspace. The selector remains visible when the workspace has no Apps, and failed App loading provides Retry Apps rather than silently hiding the choice. App flows start with their originating App selected but can switch to the top level.

Choosing an App loads that App's Track choices and templates before Continue is enabled. Switching destinations clears the previous App's template selection and type key. The final details step shows the destination, and creation submits an App ID only when an App is selected. Paused, incomplete, or uninstalled Apps cannot be selected for new Track creation.

User-facing App/Track library, import, deletion, configuration and creation labels use **template**, not substrate architecture terminology. API paths, request fields and persistence types remain unchanged. AI-provider/model settings retain their distinct meaning.

Component coverage exercises creating under a selected App with its prescribed Track type, leaving an App flow for top-level Tracks without stale App/template fields, retrying App-list failures, and the existing manual/import/library/edit/delete flows. The shared template picker labels its App/Track scope appropriately.

### Verification

- 349 frontend files / 2,115 tests passed; final focused App/Track dialog run: 40 tests passed.
- TypeScript/production build, all 16 guards, pre-commit framework and `make verify-ci` passed. Targeted ESLint reported zero errors (15 existing effect-pattern warnings).
- Browser smoke created synthetic App **Track Destination QA** and **Inside App QA** via the destination selector. The Track breadcrumb includes the App, and the App list contains that Track.
- Starting from that App's New track action, switched to **Top-level Tracks**, created **Top-level Destination QA**, and verified the Track breadcrumb has no App. The App's Track count stayed at one.
- Saved desktop chooser evidence with both destinations visible. No original Apps, Tracks or Entries were changed.

### Scalable destination sidebar refinement

Manage Tracks now follows the Manage Apps layout: template browsing on the left,
a 19rem Your selection sidebar on the right, and an explicit details/confirmation
step. The sidebar owns Track destination selection, selected template summary and
Continue. Its action stays outside the scrolling selection area on desktop.
The layout stacks on narrow screens; no horizontal overflow was observed at 390px.

The old unbounded App dropdown is replaced by name/purpose search with at most six
rendered results and a bounded scroll area. Search filters the existing workspace
App response in memory; this is not server-side pagination. Selection collapses to
a named App summary with Change App; abandoning a new search preserves the current
selection. Top-level Tracks is always available, even if loading Apps fails.
Selecting a different destination clears incompatible App template choices.

Validation: the production picker was exercised with 1,000 synthetic Apps,
including finding App 0999, no-match recovery, keyboard location selection, and
mobile bounds. The actual Manage Tracks dialog was checked through App selection,
Continue to details with the correct App, Back, and narrow-screen continuation.
No new records were created during this refinement. Screenshot:
`.local/design-review/track-sidebar-selected-app.png` (local ignored evidence).

Export scope decision: both Apps and Tracks should offer configuration-only and
full-data options. Full-data portability includes Entries, attachments and
relations and requires a separate export/import implementation; this UI refinement
does not claim to implement that data transport.

The delete icon now sits inside the Track template's colored card, with space
reserved for its touch target and no nested buttons. Both App and Track deletion
confirmations use the same short copy: name, saved configuration removal, existing
items/data retained, and irreversible action. Browser verification opened and
cancelled the Track confirmation; no template was deleted.
