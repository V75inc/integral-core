# Exact-candidate App-focus and dashboard recheck — 2026-09-29

## Candidate and environment

- **Candidate source:** `60f6e6fa4c22181ba17bba7b7e2d6001678cafc8` (`codex/local-integration-candidate`).
- **Browser route:** local Core frontend at `http://127.0.0.1:19008`, using the candidate API.
- **Account/workspace:** existing authenticated `W5.2 QA Recheck` account and workspace.
- **Scope:** read-only inspection of the effective skills catalogue and Guyana Payroll dashboard.

## Core / Business runtime boundary

- The candidate process runs from `/private/tmp/integral-core-integration/backend`. Its effective package-path resolver returned only `/private/tmp/integral-core-integration/backend/app/packages`; the candidate checkout has no `packages/apps` directory.
- The Core Manage Apps dialog nevertheless listed CRM, Documents, Guyana Payroll, Organization, and Sales as available library records. These records are present in the reused QA database's library catalogue; their appearance does not mean the Integral Business service or its App source directory is running in this candidate process.
- No Business repository files were read or changed during this recheck. No App was installed from the dialog.

## App-focus observation

1. Settings → AI Skills initially showed `Workspace` focus, 24 skills, and 160 tools. The catalogue listed 16 Core skills and eight Guyana Payroll skills.
2. Selecting `Guyana Payroll` changed the native combobox's selected option. The eight Guyana Payroll skills remained visible, alongside the 16 Core skills; the overall count remained 24 skills and 160 tools.
3. This workspace exposed only Guyana Payroll as an App. Therefore this check confirms the selector can be changed and the selected App's skills are visible, but it does **not** prove that selecting an App excludes another App's private skills or tools. Cross-App filtering remains unqualified.

## Dashboard drill-through observation

1. The Guyana Payroll dashboard rendered no aggregate values in this QA dataset.
2. Opening `Total Base salary` → `View contributing records` displayed the `Total Base salary records` modal with the explanation that the App requires a declared query capability.
3. This confirms the candidate gives readable feedback for a governed App-query denial. It does not prove successful declared-query drill-through, contribution identity, paging, or value/scope parity.

## Fresh-container positive Core dashboard journey

The same source revision was also built into Core API and web images and run against a fresh SQLite database in a disposable container stack. The Core process loaded the separately signed Asset Register archive from `/app/extensions`; this was a Core-hosted external package, not the Integral Business service. A separate workspace-authored App (`Core QA Ledger`) and Track (`QA Metrics`) were created through the browser, and two synthetic rows were posted through the normal entry form.

1. The Overview dashboard displayed `Total entries = 2` and listed both `QA Row One` and `QA Row Two` under Recent entries.
2. `Total entries` → `View contributing records` opened the results modal. It showed `Loaded 2 of 2 matching records`, recomputed `2 (count)`, named both rows, and offered `Open filtered Track`.
3. This proves the positive open-class App dashboard path on source SHA `60f6e6fa4c22181ba17bba7b7e2d6001678cafc8`. It is separate from the package-query denial above and does not prove declared-query dashboard execution for packaged Apps.

### Container and operation boundary

- API image ID: `sha256:6da410eb64527319d7eb17069fc93ac76c8c3165893ad8a95168c0058e59e008`; web image ID: `sha256:d2366180ce6e490f31a550277486e307f862dc3605e98412958e09dfd909fa73`.
- The account's AI Models page validated and saved `Ollama (Local)` / `gemma4:e2b` without an API key. The visible assistant response was attributed to that local model.
- In the same SQLite-only container, a prompt asking the assistant to register a disposable Asset Register asset received a success-sounding response, but the Assets Track remained at zero entries after reload. Startup logs state that the production work kernel requires PostgreSQL and SQLite was detected. Therefore this is **not** operation-path evidence, and no asset mutation is claimed. A PostgreSQL-backed browser journey is still required for that operation.

## Result and qualification boundary

Exact-candidate browser evidence now covers the App-focus selector, a readable packaged-App query denial, and successful populated open-class dashboard drill-through. Cross-App skill/tool exclusion remains unqualified; packaged-App dashboards still need a declared-query data-source contract; and the SQLite container cannot qualify durable typed operations. The full C6 browser matrix and W5.2 acceptance remain open.

## App-focus follow-up in the two-App fixture

The fresh `Core Candidate Smoke` workspace was reopened at the same candidate web image after the populated dashboard journey. The Settings → AI Skills selector listed both `Asset Register` and `Core QA Ledger`. Selecting `Core QA Ledger` changed the selected focus, while the catalogue still showed 20 skills and 128 tools, including the four Asset Register skills. Those package skills do not declare `private: true`, so their presence outside the Asset Register focus is not evidence of a leak. The New workspace skill dialog contained no App-scope control; it cannot author the private App-scoped fixture needed for this browser case. The existing deterministic `test_private_app_skills_offered_outside_focus_only_to_app_users` covers the profile-composer access contract, but browser-visible private-skill isolation remains unqualified. No workspace skill or App data was created or changed during this check.
