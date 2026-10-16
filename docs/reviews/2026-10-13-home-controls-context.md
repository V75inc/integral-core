# App Home, dashboard controls and context recovery

## Observed failures

The live CRM chat created an analytics Dashboard named Home for a request for an App Home tab, then exceeded the provider context limit after approval. A prior approved update with `widgets: []` did not clear the board because HTTP serialization converted the empty list to an absent update. Dashboard deletion had no confirmation.

## Core changes

- App stores an optional workspace-owned Home composition separately from Dashboard nodes. `integral_update_app(home={title, description, widgets})` stages it through the existing App update policy, approval, scope, audit and effect path. `home: {}` restores package Home defaults. Inputs and placements are bounded, nonoverlapping, and reuse current dashboard source/schema authority validation. Package declarations remain unchanged.
- `integral_read_app_home` exposes the real Home and authorized live values. The dashboard skill explicitly distinguishes Home from named analytics dashboards. The frontend discovers Home updates periodically and renders them in its separate App Home tab.
- Dashboard create/update serialization preserves empty widget arrays. Clearing a board is an update, not a deletion.
- Dashboard delete now requires a named confirmation; Cancel leaves the board intact. Pending layout autosaves are cancelled only after confirmation. Toolbar selection and action groups wrap with available width; creation is labelled New dashboard.
- Long-history compaction uses a byte trigger independently of token estimates. Summary input discards completed-turn tool payloads before its model call. The current user/typed host event remains exact, and full scoped checkpoints remain searchable. Dashboard suggestions no longer repeat full output schemas per Track.

## Qualification

- Backend cohort: 675 passed, 43 skipped, five warnings; covers native harness, App/Home/dashboard API and binding paths. Skipped fixtures/infrastructure lanes are not release evidence.
- Targeted dashboard frontend suite: 28 passed, including deletion cancellation; TypeScript passed.
- Configured backend mypy passed (713 files); configured flake8 and whitespace checks passed. Capability map regenerated.
- Browser confirmed fresh bundle `index-BkcZF5hW.js`, working delete confirmation, and cancellation without deletion.
- Read-only continuation of the previously stalled long chat succeeded: actual Home was null; the agent distinguished it from the Dashboard named Home. The first ordinary model request used 4,709 input tokens; largest recorded ordinary request 12,020. Seven recorded calls, 29.9 seconds total, 55,492 cumulative tokens; provider prices were unavailable. This is a recovery probe, not a general latency/accounting benchmark.
- Core wheel SHA256 `b6ebfd06c69eb59148b838de012bfd8a37a81d64a3f8f4ff9504529a1871fab9`; installed locally on the existing Business stack. Database backup: primary `.integral/local-deployment/home-controls-pre-update-20261013.dump`.

No user business records were intentionally changed during qualification; the existing Dashboard named Home remains intact. No approval was replayed. Source changes remain uncommitted; PRs unchanged. A real workspace Home is not automatically created or substituted for a user's existing dashboard.

Additional Home/API regression cohort: five tests passed, including actual HTTP persistence/readback of a distinct Home without Dashboard nodes, restoration of defaults, and clearing a dashboard with an explicit empty widget list. These tests used isolated test storage.


## Streamlined dashboard controls follow-up

The deployed control bar now uses one labeled native dashboard selector with a Default marker and a New dashboard option. Layout editing stays visible; rename, set default and confirmed deletion use the shared action menu. Create and rename use a named dialog with cancellation and pending/error handling. Controls wrap based on available content width, including the assistant dock.

Eight targeted frontend tests passed; TypeScript and the Business-overlay production build passed. Browser checked rename and new-dashboard dialogs without saving and verified existing live widget values. Local web bundle: `index-DhKbjOMz.js`. Existing dashboard and records were preserved. The local Python wheel identity above predates this frontend-only follow-up.

Historical uncommitted/PR statements above describe the qualification time. Subsequent commits preserve the source and reports; raw local logs, screenshots and runtime artifacts remain outside the source commits.


## Commit qualification

The CI-faithful backend smoke gate passed in a standalone run. The full default-selected backend suite (6,457 collected cases, excluding the configured domain/slow lanes) subsequently completed with exit status zero; infrastructure-dependent skips remain unqualified. All 1,741 frontend tests passed, and the final eight-test toolbar rerun passed after typography cleanup. Pinned formatting, ESLint, TypeScript, staged whitespace and pre-commit substrate guards passed.

The first broad runs were interrupted to diagnose failures: three stale tool-count contracts were corrected for the new App Home capability, while graph-context cases passed in the standalone 80-test retry. The final full backend run passed without failures. No admission, permission or deadline boundary was weakened. Raw logs remain local.
