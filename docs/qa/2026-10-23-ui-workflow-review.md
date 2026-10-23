# Local UI and workflow review — 23 October 2026

Reviewed the isolated preview at http://127.0.0.1:9155, backed by port 4473, starting from commit `27e9549f9133e6f0835c2ca2a7545e8b4a3f708b`. The personal installation and primary checkout were preserved. This review covers desktop and a 390 × 844 viewport.

## Refinements implemented

| Finding | Change | Verification |
| --- | --- | --- |
| An App with no configurable options offered an empty settings dialog. | Hide the header control when neither configurable fields nor function-access controls exist. Preserve access to historical grants; open function-only Apps directly to function access. | Browser readback on Reference SDK Home and Settings Review QA; schema and function-access regression tests. |
| Settings could be saved without changes, and feedback appeared inline. | Disable Save until values change; use success/error toasts; preserve drafts on failure and offer retry after a load failure. | Browser save/reopen persisted `QA`; unchanged Save disabled. Tests cover save, revert, failure, retry, and empty forms. |
| Mobile Track continuation fell below the library. | Put Continue in the fixed footer, show placement before the library, and require a template before continuing with a top-level Track. | Browser selected Reading List QA and reached Track details; mobile and App-context tests. |
| Completed App setup still showed “needs settings” in results. | Update the installed row after confirmed completion; distinguish queued setup from completion. | Installation transition regression test. |
| Successive App setup forms could retain the previous App's draft. | Key the setup component by App ID so each App starts with its own declared defaults. | Component lifecycle isolation; installation suite. |

Installation progress copy now says that settings are saved and setup is finishing, without implementation terminology.

## Remaining opportunities

- Make App and Track exports easier to discover within the management dialogs, alongside installed items. Exports currently require finding a separate action outside the catalog.
- Reduce repetition in AI model routing summaries. Keep the selected models and routing mode prominent, with technical limits in expandable details.
- Improve App Home loading transitions so the initial metadata load does not briefly resemble an empty dashboard.

These are follow-up recommendations, not claims that those flows are broken.

## Validation and limits

- Full frontend suite: **351 files, 2,143 tests passed**.
- TypeScript check and production build: passed. The build reports the existing large-chunk advisory.
- Browser walkthrough: Apps catalog/details/install/settings, Tracks placement and continuation, native App Home, AI model settings, and chat layout.
- The provider in this preview is a synthetic fixture. This walkthrough does not qualify real-provider response quality or latency.
- Review refinements are included in PR #170.
