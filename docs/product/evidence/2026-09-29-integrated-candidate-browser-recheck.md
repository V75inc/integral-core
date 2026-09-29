# Integrated candidate browser recheck — 2026-09-29

## Candidate and environment

- **Source revision:** `3da6199ff4aa1c0405562f5d9f9d348e7b504786` (`codex/local-integration-candidate`).
- **Scope:** fresh-account Core workspace creation, first Track persistence, and effective Core Skills catalogue visibility.
- **Deployment:** isolated Compose project `integral-core-candidate-3da`; API `127.0.0.1:14100`, web `127.0.0.1:19106`, Postgres `127.0.0.1:15434`.
- **Local image IDs:** API `sha256:35ae283e2c4c942d224306260230dce8724eed523fd0317361abcdfc84769488`; web `sha256:63c418cded6f7dfc16a3ce27ccde53cade7861a18d5b8808080206e8003048c5`; Postgres `sha256:fa3d9bb7ee77f5c1f0bfb009a9df30243c040896825f3033b09a77101bb2ca95`.
- **Model/provider state:** no model-provider credentials were used. Email provider was `console`, so signup verification mail remained local; verification was skipped using the product's visible “Skip for now” control.
- **Fixture:** synthetic account `core-candidate-smoke-20260929@example.com` and workspace `Core Candidate Smoke`, created in the isolated candidate database.

## Browser observations

1. Signup completed and created a fresh workspace. The Tracks page showed zero Tracks.
2. Created the default Track `Candidate Persistence Smoke` in the browser. The Tracks page immediately displayed one Track and a success notice without a manual refresh.
3. Opened its detail page and reloaded it. The Track title, description, and detail page were still present after reload.
4. Opened Settings → AI Skills. The effective workspace catalogue rendered 16 Core skills and 122 Core tools, with the `Workspace` App focus selected. Skill rows reported `Available`.
5. Created the disposable App `Synthetic Dashboard Qualification`, its `Qualification Records` Track, and two synthetic entries in the browser. `Suggest for this App` created an Overview dashboard with count, status, and recent-activity widgets. The count drill-through returned both records; selecting the `active` status group returned both records and showed the selected widget value and recomputed count as 2.
6. Inspected the chart drill-through dialog visually. Its title and content have clear horizontal and vertical gutters inside the modal. The chart's separate “View contributing records” all-groups action has no single current bar value to compare and displayed `unavailable`; selecting the `active: 2` group displays the live value 2. The modal's records and padding rendered correctly in both cases.

## Limits

- This run proves the integrated candidate's fresh-account Track journey, Core Skills catalogue visibility, and a small synthetic App dashboard flow. It did not exercise large-result continuation or a schema-rich business App.
- W5.4's separate 102-entry drill-through browser evidence is in [W5.4 package evidence](packages/W5.4.yaml); this two-entry dashboard check does not replace its page-boundary fixture.
- No model conversation, transport-parity journey, responsive audit, accessibility audit, or screenshot-baseline comparison was run.
- This local integrated candidate is not on `main` and is not a release. The package PRs and Product Owner release review remain separate gates.
