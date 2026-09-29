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

## Limits

- This run proves the integrated candidate's fresh-account Track journey and Core Skills catalogue visibility only. It did not create a populated App or exercise dashboard dialogs in this fresh workspace.
- W5.4's separate 102-entry drill-through browser evidence is in [W5.4 package evidence](packages/W5.4.yaml); its synthetic populated-App fixture is not represented as a fresh-account journey on this candidate.
- No model conversation, transport-parity journey, responsive audit, accessibility audit, or screenshot-baseline comparison was run.
- This local integrated candidate is not on `main` and is not a release. The package PRs and Product Owner release review remain separate gates.
