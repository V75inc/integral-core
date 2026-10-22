# Manage Apps redesign qualification

October 22, 2026. Extends PR #168 after `f939d1c0` (the earlier QA, display-name,
Home model-state and dependency-audit changes were already committed and pushed).

## Approved design and implementation

The approved interactive catalog/review design is now implemented in the real
Manage Apps dialog using Core's Modal, ViewTabs, Surface, Text, AppIcon, Input
and Button primitives. The user additionally requested a prominent **Create your
own app** action; it is an outlined plus-icon button in the persistent footer,
and the creation form uses the same wording.

- Separate Browse library and Installed tabs with counts and keyboard navigation.
- Search by name, description/purpose or package slug.
- Compact cards with explicit Add/Selected controls; package details and saved
  template deletion are progressively disclosed through View details.
- Persistent selection review, retaining choices through search and tab changes.
- Custom names and descriptions plus example-data selection in the review pane.
- A separate confirmation step before the existing batch install API is invoked.
- Dedicated mobile review with Back to library navigation; creation remains
  visible in both views. No horizontal overflow at the checked 390px viewport.
- Existing install settings, partial results, host denial recovery, workspace
  visibility, owner-only uninstall and dependency/data preflights are retained.
- Refreshing the library clears stale workspace data, and in-flight installation
  cannot be dismissed into an ambiguous state.

The new presentational `AppManagerLibrary.tsx` composes the browsing/review UI;
`AppManagerDialog.tsx` retains the existing lifecycle and authority handling.
No backend, provider credential or deployment changes are included.

## Verification

Final local qualification passed: **345 frontend files / 2,041 tests**, production
build, guards, pre-commit, pinned formatting, lint and TypeScript. ESLint has
zero errors; the existing warning backlog remains nonblocking. Backend behavior
is unchanged, and the parent PR head had all applicable remote CI checks green.
The pushed head's remote CI result is recorded on the PR.

Component regressions exercise explicit review before installation, selection
and custom-detail retention, no mutation while browsing/reviewing, sample-data
payloads, empty-name admission, workspace template filtering, owner-only
uninstall, dependency blockers, awaiting-settings continuation, partial/denied
results, unknown-outcome reconciliation, failed-library retry, mobile navigation
and creation callback behavior.

## Real browser evidence

Disposable local PostgreSQL and two existing synthetic QA identities; API 4469,
UI 9149. Six synthetic QA templates were imported through the ordinary scoped
Operational Model API. No production/user installation was modified.

- [Catalog and selection](screenshots/catalog.jpg): search retained two selections
  and a custom App name; Create your own app stayed visible.
- [Review](screenshots/review.jpg) and [mobile review](screenshots/mobile-review.jpg):
  final choices were shown before confirmation.
- [Mobile catalog](screenshots/mobile-catalog.jpg): 390px layout and persistent
  creation/review controls.
- [Install result](screenshots/install-result.jpg): real reviewed batch installation
  created **QA Equipment** and **QA Contacts**, with 2 installed / 0 failed.
- [Installed](screenshots/installed.jpg): both Apps appeared in the correct
  workspace with Open App and owner uninstall controls.
- [Creation form](screenshots/create-your-own.jpg): the prominent action opened
  the real form; the form was cancelled without creating an additional App.

Required-settings and uninstall-denial cases are covered by component regression
rather than destructive live browser actions. No external model was called.
Integral One's composed UI must be rebuilt to consume the upstream Core change;
this PR is not an installed desktop release or deployment.
