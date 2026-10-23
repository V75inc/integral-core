# Track destinations and plain-language template labels

Manage Tracks now starts with **Add track to**. Users may choose **Top-level Tracks** or an existing App in the active workspace. The selector remains visible when the workspace has no Apps, and failed App loading provides Retry Apps rather than silently hiding the choice. App flows start with their originating App selected but can switch to the top level.

Choosing an App loads that App's Track choices and templates before Continue is enabled. Switching destinations clears the previous App's template selection and type key. The final details step shows the destination, and creation submits an App ID only when an App is selected. Paused, incomplete, or uninstalled Apps cannot be selected for new Track creation.

User-facing App/Track library, import, deletion, configuration and creation labels use **template**, not substrate architecture terminology. API paths, request fields and persistence types remain unchanged. AI-provider/model settings retain their distinct meaning.

Component coverage exercises creating under a selected App with its prescribed Track type, leaving an App flow for top-level Tracks without stale App/template fields, retrying App-list failures, and the existing manual/import/library/edit/delete flows. The shared template picker labels its App/Track scope appropriately.

## Verification

- 349 frontend files / 2,115 tests passed; final focused App/Track dialog run: 40 tests passed.
- TypeScript/production build, all 16 guards, pre-commit framework and `make verify-ci` passed. Targeted ESLint reported zero errors (15 existing effect-pattern warnings).
- Browser smoke created synthetic App **Track Destination QA** and **Inside App QA** via the destination selector. The Track breadcrumb includes the App, and the App list contains that Track.
- Starting from that App's New track action, switched to **Top-level Tracks**, created **Top-level Destination QA**, and verified the Track breadcrumb has no App. The App's Track count stayed at one.
- Saved desktop chooser evidence with both destinations visible. No original Apps, Tracks or Entries were changed.
