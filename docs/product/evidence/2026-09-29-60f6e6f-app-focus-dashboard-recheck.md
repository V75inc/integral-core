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
3. This confirms the candidate gives readable feedback for a governed App-query denial. It does not prove successful declared-query drill-through, contribution identity, paging, or value/scope parity. The synthetic positive drill-through journey remains open.

## Result and qualification boundary

This is exact-candidate evidence for an App-focus selection, a readable denied drill-through, and the current empty-state modal. It is not a passing result for cross-App catalogue isolation or successful dashboard drill-through. No payroll tools were invoked and no workspace records were changed. The full C6 browser matrix and W5.2 acceptance remain open.
